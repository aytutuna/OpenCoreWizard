# Keys that open the BIOS setup and the one-time boot menu, by PC or board maker.
# Most PCs also show them on the first screen at power-on. Matched against the
# maker and model names Windows reports; the first pattern that matches wins.
import re

# (pattern, desktop keys, laptop keys); keys are (setup, boot menu)
makers = (
    (r"ASUS", ("Delete or F2", "F8"), ("F2", "Esc")),
    (r"ASRock", ("F2 or Delete", "F11"), ("F2", "F11")),
    (r"Micro-Star|\bMSI\b", ("Delete", "F11"), ("Delete", "F11")),
    (r"Gigabyte|AORUS", ("Delete", "F12"), ("F2", "F12")),
    (r"Biostar", ("Delete", "F9"), ("Delete", "F9")),
    (r"Supermicro", ("Delete", "F11"), ("Delete", "F11")),
    (r"\bDell\b|Alienware", ("F2", "F12"), ("F2", "F12")),
    (r"Hewlett|\bHP\b", ("F10", "F9 (or Esc, then F9)"), ("F10", "F9 (or Esc, then F9)")),
    (r"Lenovo", ("F1", "F12"), ("F2 (on some models Fn+F2)", "F12 (on some models Fn+F12)")),
    (r"\bAcer\b", ("Delete or F2", "F12"), ("F2", "F12 (turn on F12 Boot Menu in the BIOS first)")),
    (r"Intel.*NUC|\bNUC\b", ("F2", "F10"), ("F2", "F10")),
    (r"Huawei|Honor|Xiaomi|Redmi", ("F2", "F12"), ("F2", "F12")),
)
DEFAULT = {"setup": "Delete or F2", "boot_menu": "F12, F11, F8 or Esc (the first screen usually shows which)", "known": False}


def for_pc(names, laptop=False):
    text = " ".join(str(name) for name in names if name)
    for pattern, desktop_keys, laptop_keys in makers:
        if re.search(pattern, text, re.IGNORECASE):
            setup, boot_menu = laptop_keys if laptop else desktop_keys
            return {"setup": setup, "boot_menu": boot_menu, "known": True}
    return dict(DEFAULT)
