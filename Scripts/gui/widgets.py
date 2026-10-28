import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

COLORS = {
    "green": "#1a7f37",
    "red": "#c62828",
    "yellow": "#9a6700",
    "orange": "#b26a00",
    "cyan": "#00838f",
    "grey": "#6e7781"
}


def mono_font(root, size=None):
    try:
        base = tkfont.Font(root=root, name="TkFixedFont", exists=True)
    except tk.TclError:
        return ("Courier", size or 10)
    font = base.copy()
    if "Consolas" in tkfont.families(root):
        font.configure(family="Consolas")
    if size:
        font.configure(size=size)
    return font


def scrolled_text(parent, height=10, width=80, mono=False, wrap="word"):
    frame = ttk.Frame(parent)
    text = tk.Text(frame, height=height, width=width, wrap=wrap, borderwidth=1, relief="solid", padx=6, pady=4)
    text.configure(font=mono_font(parent) if mono else default_font(parent))
    scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=scrollbar.set)
    text.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    for name, color in COLORS.items():
        text.tag_configure(name, foreground=color)
    text.tag_configure("bold", font=bold_font(parent))
    text.configure(state="disabled")
    return frame, text


def scrolled_frame(parent):
    outer = ttk.Frame(parent)
    canvas = tk.Canvas(outer, borderwidth=0, highlightthickness=0)
    scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    inner = ttk.Frame(canvas)
    window = canvas.create_window((0, 0), window=inner, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")
    outer.rowconfigure(0, weight=1)
    outer.columnconfigure(0, weight=1)
    inner.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width), add="+")

    def wheel(event):
        canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
    def leave(event):
        # Leave also fires when the pointer moves onto a child of the frame
        widget = canvas.winfo_containing(event.x_root, event.y_root)
        if widget is None or not str(widget).startswith(str(outer)):
            canvas.unbind_all("<MouseWheel>")
    inner.bind("<Enter>", lambda _: canvas.bind_all("<MouseWheel>", wheel))
    inner.bind("<Leave>", leave)
    return outer, canvas, inner


def set_text(text, content, tag=None):
    text.configure(state="normal")
    text.delete("1.0", "end")
    if content:
        text.insert("end", content, (tag,) if tag else ())
    text.configure(state="disabled")


def append_lines(text, lines, tag=None, bullet=""):
    text.configure(state="normal")
    for line in lines:
        text.insert("end", "{}{}\n".format(bullet, line), (tag,) if tag else ())
    text.configure(state="disabled")


def default_font(root):
    try:
        return tkfont.Font(root=root, name="TkDefaultFont", exists=True)
    except tk.TclError:
        return ("TkDefaultFont", 9)


def bold_font(root):
    try:
        font = tkfont.Font(root=root, name="TkDefaultFont", exists=True).copy()
    except tk.TclError:
        return ("TkDefaultFont", 9, "bold")
    font.configure(weight="bold")
    return font


def sized_font(root, delta=0, bold=False):
    try:
        font = tkfont.Font(root=root, name="TkDefaultFont", exists=True).copy()
    except tk.TclError:
        return ("TkDefaultFont", 9 + delta, "bold" if bold else "normal")
    font.configure(size=abs(int(font.cget("size"))) + delta, weight="bold" if bold else "normal")
    return font


def heading_font(root, delta=4):
    try:
        font = tkfont.Font(root=root, name="TkDefaultFont", exists=True).copy()
    except tk.TclError:
        return ("TkDefaultFont", 12, "bold")
    font.configure(weight="bold", size=abs(int(font.cget("size"))) + delta)
    return font


def tree(parent, columns, headings, widths=None, height=8, show="headings"):
    frame = ttk.Frame(parent)
    view = ttk.Treeview(frame, columns=columns, show=show, height=height, selectmode="browse")
    for index, column in enumerate(columns):
        view.heading(column, text=headings[index], anchor="w")
        width = (widths or {}).get(column, 120)
        view.column(column, width=width, minwidth=40, anchor="w", stretch=True)
    scrollbar = ttk.Scrollbar(frame, orient="vertical", command=view.yview)
    view.configure(yscrollcommand=scrollbar.set)
    view.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    for name, color in COLORS.items():
        view.tag_configure(name, foreground=color)
    return frame, view


def clear_tree(view):
    for item in view.get_children(""):
        view.delete(item)
