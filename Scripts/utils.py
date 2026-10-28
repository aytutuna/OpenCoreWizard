import os
import sys
import json
import plistlib
import shutil
import re
import binascii
import subprocess
import pathlib
import zipfile
import tempfile
import threading

from Scripts import explain

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
DEFAULT_VALUE = explain.DEFAULT_VALUE

class HeadlessExit(BaseException):
    pass

class HeadlessPromptLoop(BaseException):
    pass

class GuiExit(BaseException):
    def __init__(self, message="Program exit requested", reason="exit"):
        super().__init__(message)
        self.reason = reason

class OutputProxy:
    def __init__(self, fallback=None):
        self.fallback = fallback

    @property
    def encoding(self):
        return getattr(self.fallback, "encoding", None) or "utf-8"

    @property
    def errors(self):
        return getattr(self.fallback, "errors", None) or "replace"

    def write(self, text):
        if isinstance(text, bytes):
            text = text.decode("utf-8", "replace")
        elif not isinstance(text, str):
            text = str(text)
        Utils.capture_output(text)
        sink = Utils.output_sink
        if sink is not None:
            sink(text)
        elif self.fallback is not None:
            self.fallback.write(text)
        return len(text)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        if Utils.output_sink is None and self.fallback is not None:
            try:
                self.fallback.flush()
            except Exception:
                pass

    def isatty(self):
        if Utils.output_sink is not None or self.fallback is None:
            return False
        try:
            return self.fallback.isatty()
        except Exception:
            return False

    def writable(self):
        return True

    def readable(self):
        return False

    def fileno(self):
        if self.fallback is None:
            raise OSError("no file descriptor")
        return self.fallback.fileno()

class Utils:
    prompt_handler = None
    output_sink = None
    progress_handler = None
    capture_limit = 20000
    _captured = []
    _captured_size = 0
    _capture_lock = threading.Lock()

    headless_answers = None
    headless_repeat_limit = 20
    headless_prompt_limit = 50
    headless_total_limit = 300
    _headless_last_prompt = None
    _headless_repeat_count = 0
    _headless_prompt_counts = {}
    _headless_total_count = 0

    presets = []
    _preset_counts = {}
    accept_defaults = False
    _default_prompt = None
    _default_count = 0

    explained_prompt = None
    decisions = []
    _stage_keys = ()

    def __init__(self, script_name = "OpenCore Wizard"):
        self.script_name = script_name

    @classmethod
    def set_headless(cls, answers):
        cls.headless_answers = answers
        cls._headless_last_prompt = None
        cls._headless_repeat_count = 0
        cls._headless_prompt_counts = {}
        cls._headless_total_count = 0
        cls.explained_prompt = None

    @classmethod
    def set_presets(cls, mapping):
        cls.presets = [(str(key).lower(), "" if value is None else str(value)) for key, value in (mapping or {}).items() if key]
        cls._preset_counts = {}

    @classmethod
    def set_accept_defaults(cls, enabled):
        cls.accept_defaults = bool(enabled)
        cls._default_prompt = None
        cls._default_count = 0

    @classmethod
    def explain(cls, prompt):
        key = explain.decision_for_prompt(prompt)
        if key is not None and prompt != cls.explained_prompt:
            print("")
            print(explain.render_decision(key))
            print("")
        cls.explained_prompt = prompt

    @classmethod
    def record_decision(cls, prompt, answer):
        key = explain.decision_for_prompt(prompt)
        if key is None:
            return
        # a prompt asked again replaces its earlier answer
        cls.decisions = [item for item in cls.decisions if item[2] != prompt] + [(key, answer, prompt)]

    @classmethod
    def decision_lines(cls, reasons=None):
        reasons = reasons or {}
        return [explain.decision_line(key, answer, prompt, reasons.get(key)) for key, answer, prompt in cls.decisions]

    @classmethod
    def stage_notes(cls, step):
        keys = explain.STEP_STAGES.get(step, ())
        if not keys or set(keys) <= set(cls._stage_keys):
            return []
        cls._stage_keys = keys
        return [explain.stages[key] for key in keys]

    @classmethod
    def automatic_answer(cls, prompt):
        lowered = prompt.lower()
        for key, value in cls.presets:
            if key in lowered:
                count = cls._preset_counts[prompt] = cls._preset_counts.get(prompt, 0) + 1
                if count <= 3:
                    return value
                # the prompt keeps rejecting the preset: stop answering it automatically
                return None
        if cls.accept_defaults and (DEFAULT_VALUE.search(prompt) or lowered.strip().startswith("press enter")):
            if prompt == cls._default_prompt:
                cls._default_count += 1
            else:
                cls._default_prompt = prompt
                cls._default_count = 1
            # an empty answer asked for again means the default was not accepted; hand over instead of spinning
            if cls._default_count <= 3:
                return ""
        return None

    @classmethod
    def is_headless(cls):
        return cls.headless_answers is not None

    @classmethod
    def set_prompt_handler(cls, handler):
        cls.prompt_handler = handler
        cls.take_captured()

    @classmethod
    def set_output_sink(cls, sink):
        cls.output_sink = sink

    @classmethod
    def set_progress_handler(cls, handler):
        cls.progress_handler = handler

    @classmethod
    def is_gui(cls):
        return cls.prompt_handler is not None

    @classmethod
    def install_output_proxy(cls):
        previous = (sys.stdout, sys.stderr)
        if not isinstance(sys.stdout, OutputProxy):
            sys.stdout = OutputProxy(sys.stdout)
        if not isinstance(sys.stderr, OutputProxy):
            sys.stderr = OutputProxy(sys.stderr)

        def restore():
            sys.stdout, sys.stderr = previous
        return restore

    @classmethod
    def capture_output(cls, text):
        if cls.prompt_handler is None or not text:
            return
        with cls._capture_lock:
            cls._captured.append(text)
            cls._captured_size += len(text)
            while cls._captured_size > cls.capture_limit and len(cls._captured) > 1:
                cls._captured_size -= len(cls._captured.pop(0))

    @classmethod
    def take_captured(cls):
        with cls._capture_lock:
            text = "".join(cls._captured)
            cls._captured = []
            cls._captured_size = 0
        text = ANSI_ESCAPE.sub("", text)
        return text[-cls.capture_limit:]

    def headless_answer(self, prompt):
        answers = Utils.headless_answers
        prompt_text = str(prompt)

        if prompt_text == Utils._headless_last_prompt:
            Utils._headless_repeat_count += 1
        else:
            Utils._headless_last_prompt = prompt_text
            Utils._headless_repeat_count = 1
        if Utils._headless_repeat_count > Utils.headless_repeat_limit:
            raise HeadlessPromptLoop("Prompt repeated {} times without progress: {}".format(Utils._headless_repeat_count - 1, prompt_text.strip()))

        Utils._headless_total_count += 1
        Utils._headless_prompt_counts[prompt_text] = Utils._headless_prompt_counts.get(prompt_text, 0) + 1
        if Utils._headless_total_count > Utils.headless_total_limit:
            raise HeadlessPromptLoop("More than {} prompts in one headless run".format(Utils.headless_total_limit))
        if Utils._headless_prompt_counts[prompt_text] > Utils.headless_prompt_limit:
            raise HeadlessPromptLoop("Prompt asked more than {} times in one headless run: {}".format(Utils.headless_prompt_limit, prompt_text.strip()))

        lowered = prompt_text.lower()
        for key, value in (answers.get("prompts") or {}).items():
            if key and key.lower() in lowered:
                answer = str(value)
                print("{}{}".format(prompt_text, answer))
                return answer

        answer = str(answers.get("default", ""))
        print("{}{}    [headless default]".format(prompt_text, answer))
        return answer

    def clean_temporary_dir(self):
        temporary_dir = tempfile.gettempdir()
        
        for file in os.listdir(temporary_dir):
            if file.startswith("ocs_"):
    
                if not os.path.isdir(os.path.join(temporary_dir, file)):
                    continue

                try:
                    shutil.rmtree(os.path.join(temporary_dir, file))
                except Exception as e:
                    pass
    
    def get_temporary_dir(self):
        return tempfile.mkdtemp(prefix="ocs_")

    def write_file(self, file_path, data):
        file_extension = os.path.splitext(file_path)[1]

        with open(file_path, "w" if file_extension == ".json" else "wb") as file:
            if file_extension == ".json":
                json.dump(data, file, indent=4)
            else:
                if file_extension == ".plist":
                    data = plistlib.dumps(data)

                file.write(data)

    def read_file(self, file_path):
        if not os.path.exists(file_path):
            return None

        file_extension = os.path.splitext(file_path)[1]

        with open(file_path, "r" if file_extension == ".json" else "rb") as file_handle:
            if file_extension == ".plist":
                data = plistlib.load(file_handle)
            elif file_extension == ".json":
                data = json.load(file_handle)
            elif file_extension == ".ids":
                data = {}
                current_vendor = None

                for line in file_handle.read().decode().splitlines():
                    if not line.strip() or line.startswith("#"):
                        continue

                    if not line.startswith("\t"):
                        parts = line.split(maxsplit=1)
                        if len(parts) == 2:
                            current_vendor = parts[0].upper()
                            data[current_vendor] = {"name": parts[1], "devices": {}}
                    elif current_vendor:
                        parts = line.strip().split(maxsplit=1)
                        if len(parts) == 2:
                            device_id, device_name = parts
                            data[current_vendor]["devices"][device_id.upper()] = device_name
            else:
                data = file_handle.read()
            return data

    def find_matching_paths(self, root_path, extension_filter=None, name_filter=None, type_filter=None):

        def is_valid_item(name):
            if name.startswith("."):
                return False
            if extension_filter and not name.lower().endswith(extension_filter.lower()):
                return False
            if name_filter and name_filter not in name:
                return False
            return True
        
        found_paths = []

        for root, dirs, files in os.walk(root_path):
            relative_root = root.replace(root_path, "")[1:]

            if type_filter in (None, "dir"):
                for d in dirs:
                    if is_valid_item(d):
                        found_paths.append((os.path.join(relative_root, d), "dir"))

            if type_filter in (None, "file"):
                for file in files:
                    if is_valid_item(file):
                        found_paths.append((os.path.join(relative_root, file), "file"))

        return sorted(found_paths, key=lambda path: path[0])

    def create_folder(self, path, remove_content=False):
        if os.path.exists(path):
            if remove_content:
                shutil.rmtree(path)
                os.makedirs(path)
        else:
            os.makedirs(path)

    def hex_to_bytes(self, string):
        try:
            hex_string = re.sub(r'[^0-9a-fA-F]', '', string)

            if len(re.sub(r"\s+", "", string)) != len(hex_string):
                return string
            
            return binascii.unhexlify(hex_string)
        except binascii.Error:
            return string
    
    def int_to_hex(self, number):
        return format(number, '02X')
    
    def to_little_endian_hex(self, hex_string):
        hex_string = hex_string.lower().lstrip("0x")

        return ''.join(reversed([hex_string[i:i+2] for i in range(0, len(hex_string), 2)])).upper()
    
    def string_to_hex(self, string):
        return ''.join(format(ord(char), '02X') for char in string)
    
    def extract_zip_file(self, zip_path, extraction_directory=None):
        if extraction_directory is None:
            extraction_directory = os.path.splitext(zip_path)[0]
        
        os.makedirs(extraction_directory, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extraction_directory)

    def get_unique_key(self, base_key, dictionary):
        if base_key not in dictionary:
            return base_key

        counter = 1
        unique_key = "{}_#{}".format(base_key, counter)

        while unique_key in dictionary:
            counter += 1
            unique_key = "{}_#{}".format(base_key, counter)

        return unique_key

    def contains_any(self, data, search_item, start=0, end=None):
        return next((item for item in data[start:end] if item.lower() in search_item.lower()), None)

    def normalize_path(self, path):
        path = re.sub(r'^[\'"]+|[\'"]+$', '', path)
        
        path = path.strip()
        
        path = os.path.expanduser(path)
        
        if os.name == 'nt':
            path = path.replace('\\', '/')
            path = re.sub(r'/+', '/', path)
        else:
            path = path.replace('\\', '')
        
        path = os.path.normpath(path)
        
        return str(pathlib.Path(path).resolve())
    
    def parse_darwin_version(self, darwin_version):
        major, minor, patch = map(int, darwin_version.split('.'))
        return major, minor, patch
    
    def open_folder(self, folder_path):
        if Utils.is_headless() or Utils.is_gui():
            return

        if os.name == 'posix':
            if 'darwin' in os.uname().sysname.lower():
                subprocess.run(['open', folder_path])
            else:
                subprocess.run(['xdg-open', folder_path])
        elif os.name == 'nt':
            os.startfile(folder_path)

    def request_input(self, prompt="Press Enter to continue..."):
        if Utils.prompt_handler is None:
            Utils.explain(str(prompt))
            answer = Utils.automatic_answer(str(prompt))
            if answer is not None:
                print("{}{}".format(prompt, answer or "[Enter]"))
                if Utils.accept_defaults:
                    Utils.record_decision(str(prompt), answer)
                return answer

        if Utils.is_headless():
            answer = self.headless_answer(prompt)
            if Utils.accept_defaults:
                Utils.record_decision(str(prompt), answer)
            return answer

        handler = Utils.prompt_handler
        if handler is not None:
            prompt_text = str(prompt)
            answer = handler(prompt_text, Utils.take_captured())
            answer = "" if answer is None else str(answer)
            print("{}{}".format(prompt_text, answer or "[Enter]"))
            Utils.take_captured()
            return answer

        if sys.version_info[0] < 3:
            user_response = raw_input(prompt)
        else:
            user_response = input(prompt)
        
        if not isinstance(user_response, str):
            user_response = str(user_response)
        
        return user_response

    def progress_bar(self, title, steps, current_step_index, done=False):
        if Utils.progress_handler is not None:
            Utils.progress_handler(title, list(steps), current_step_index, done)
            if done:
                print("{}: done".format(title))
            elif 0 <= current_step_index < len(steps):
                print("[{}/{}] {}...".format(current_step_index + 1, len(steps), steps[current_step_index]))
                for note in Utils.stage_notes(steps[current_step_index]):
                    print(explain.wrap("      " + note, 100, "      "))
            return

        self.head(title)
        print("")
        if done:
            for step in steps:
                print("  [\033[92m✓\033[0m] {}".format(step))
        else:
            for i, step in enumerate(steps):
                if i < current_step_index:
                    print("  [\033[92m✓\033[0m] {}".format(step))
                elif i == current_step_index:
                    print("  [\033[1;93m>\033[0m] {}...".format(step))
                else:
                    print("  [ ] {}".format(step))
            if 0 <= current_step_index < len(steps):
                notes = Utils.stage_notes(steps[current_step_index])
                if notes:
                    print("")
                for note in notes:
                    print(explain.wrap("  " + note, 68, "  "))
        print("")

    def head(self, text = None, width = 68, resize=True):
        if Utils.is_headless():
            print("")
            print("== {} ==".format(text if text is not None else self.script_name))
            return

        if Utils.is_gui():
            Utils.take_captured()
            print("")
            print("== {} ==".format(text if text is not None else self.script_name))
            return

        if resize:
            self.adjust_window_size()
        os.system('cls' if os.name=='nt' else 'clear')
        Utils.explained_prompt = None
        if text == None:
            text = self.script_name
        separator = "═" * (width - 2)
        title = " {} ".format(text)
        if len(title) > width - 2:
            title = title[:width-4] + "..."
        title = title.center(width - 2)
        
        print("╔{}╗\n║{}║\n╚{}╝".format(separator, title, separator))
    
    def adjust_window_size(self, content=""):
        if Utils.is_headless() or Utils.is_gui():
            return

        lines = content.splitlines()
        rows = len(lines)
        cols = max(len(line) for line in lines) if lines else 0
        print('\033[8;{};{}t'.format(max(rows+6, 30), max(cols+2, 100)))

    def exit_program(self):
        if Utils.is_headless():
            raise HeadlessExit("Program exit requested")
        # express runs inside the menu, so an exit request ends the run, not the program
        if Utils.is_gui() or Utils.accept_defaults:
            raise GuiExit("Program exit requested")

        self.head()
        width = 68
        print("")
        print("For more information, to report errors, or to contribute to the product:".center(width))
        print("")

        separator = "─" * (width - 4)
        print(f" ┌{separator}┐ ")
        
        contacts = {
            "Issues": "https://github.com/aytutuna/OpenCoreWizard/issues",
            "Based on": "https://github.com/lzhoang2801/OpCore-Simplify"
        }
        
        for platform, link in contacts.items():
            line = f" * {platform}: {link}"
            print(f" │{line.ljust(width - 4)}│ ")

        print(f" └{separator}┘ ")
        print("")
        print("Thank you for using our program!".center(width))
        print("")
        self.request_input("Press Enter to exit.".center(width))
        sys.exit(0)