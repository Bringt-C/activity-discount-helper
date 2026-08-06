from __future__ import annotations

import ctypes
import math
import re
import sys
import threading
import tkinter as tk
from tkinter import font as tkfont
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import Image, ImageGrab, ImageTk


APP_NAME = "活动降幅助手"
VK_F8 = 0x77

THEMES = {
    "经典蓝": {"bg": "#EEF4F8", "card": "#FFFFFF", "text": "#17324D", "muted": "#6B7F91", "accent": "#1686B8", "hover": "#0F6F9C", "line": "#D4E0E8", "input": "#FFFFFF"},
    "翡翠绿": {"bg": "#EDF7F2", "card": "#FFFFFF", "text": "#18392D", "muted": "#698076", "accent": "#258A62", "hover": "#1C704F", "line": "#D1E4DA", "input": "#FFFFFF"},
    "暖阳橙": {"bg": "#FFF5E9", "card": "#FFFCF8", "text": "#493224", "muted": "#8A7464", "accent": "#E57C2A", "hover": "#C9651D", "line": "#EAD9C9", "input": "#FFFFFF"},
    "深邃黑": {"bg": "#171B22", "card": "#232832", "text": "#F2F5F8", "muted": "#A8B1BE", "accent": "#4EA3E3", "hover": "#368AC9", "line": "#3B4350", "input": "#2B313D"},
}


def enable_high_dpi() -> None:
    """Keep screen coordinates and captured pixels aligned on scaled displays."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def decimal_or_none(value: str) -> Decimal | None:
    value = value.strip().replace(",", "")
    if not value:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def trim_decimal(value: Decimal, places: int = 4) -> str:
    quantum = Decimal(1).scaleb(-places)
    text = format(value.quantize(quantum, rounding=ROUND_HALF_UP), "f")
    return text.rstrip("0").rstrip(".")


@dataclass
class PriceCandidate:
    value: Decimal
    raw_text: str
    score: float


class PriceOCR:
    def __init__(self) -> None:
        self._engine = None

    def _load(self):
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR()
        return self._engine

    @staticmethod
    def _extract(text: str, confidence: float) -> list[PriceCandidate]:
        normalized = (
            text.upper()
            .replace("O", "0")
            .replace("I", "1")
            .replace("L", "1")
            .replace("，", ",")
            .replace("。", ".")
        )
        values: list[PriceCandidate] = []
        pattern = r"(?<![\d])(?:USD|US\$|\$|￥|¥|RMB)?\s*(\d{1,8}(?:[.,]\d{1,2})?)(?![\d])"
        for match in re.finditer(pattern, normalized):
            token = match.group(1)
            # A single comma followed by one/two digits is probably a decimal mark.
            if "," in token and "." not in token:
                head, tail = token.rsplit(",", 1)
                token = f"{head}.{tail}" if len(tail) <= 2 else head + tail
            else:
                token = token.replace(",", "")
            try:
                value = Decimal(token)
            except InvalidOperation:
                continue
            if value > 0:
                currency_bonus = 0.2 if re.search(r"USD|US\$|\$|￥|¥|RMB", match.group(0)) else 0
                decimal_bonus = 0.1 if "." in token else 0
                values.append(PriceCandidate(value, text, confidence + currency_bonus + decimal_bonus))
        return values

    def recognize(self, image: Image.Image) -> tuple[Decimal | None, str]:
        import numpy as np

        engine = self._load()
        rgb = image.convert("RGB")
        # Upscaling small text markedly improves price recognition.
        if rgb.height < 120:
            factor = max(2, min(4, math.ceil(120 / max(rgb.height, 1))))
            rgb = rgb.resize((rgb.width * factor, rgb.height * factor), Image.Resampling.LANCZOS)
        result, _ = engine(np.asarray(rgb))
        if not result:
            return None, "未识别到文字"

        candidates: list[PriceCandidate] = []
        recognized: list[str] = []
        for item in result:
            text = str(item[1]).strip()
            confidence = float(item[2])
            recognized.append(text)
            candidates.extend(self._extract(text, confidence))

        if not candidates:
            return None, "识别到：" + " / ".join(recognized)
        candidates.sort(key=lambda item: item.score, reverse=True)
        best = candidates[0]
        return best.value, "识别到：" + " / ".join(recognized)


class CaptureOverlay(tk.Toplevel):
    def __init__(self, parent: tk.Tk, screenshot: Image.Image, bounds: tuple[int, int, int, int], callback):
        super().__init__(parent)
        self.screenshot = screenshot
        self.bounds = bounds
        self.callback = callback
        self.start: tuple[int, int] | None = None
        self.rect_id = None

        left, top, width, height = bounds
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.geometry(f"{width}x{height}{left:+d}{top:+d}")
        self.attributes("-alpha", 0.28)
        self.configure(bg="#07111f")

        self.canvas = tk.Canvas(self, bg="#07111f", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            width // 2,
            42,
            text="拖动框选屏幕上的当前价格  ·  Esc 取消",
            fill="white",
            font=("Microsoft YaHei UI", 15, "bold"),
        )
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.bind("<Escape>", lambda _event: self._cancel())
        self.focus_force()
        self.grab_set()

    def _press(self, event) -> None:
        self.start = (event.x, event.y)
        self.rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#38bdf8", width=3, fill="#ffffff", stipple="gray50"
        )

    def _move(self, event) -> None:
        if self.start and self.rect_id:
            self.canvas.coords(self.rect_id, self.start[0], self.start[1], event.x, event.y)

    def _release(self, event) -> None:
        if not self.start:
            return
        x1, y1 = self.start
        x2, y2 = event.x, event.y
        crop_left, crop_right = sorted((max(0, x1), max(0, x2)))
        crop_top, crop_bottom = sorted((max(0, y1), max(0, y2)))
        if crop_right - crop_left < 8 or crop_bottom - crop_top < 8:
            self._cancel()
            return
        crop = self.screenshot.crop((crop_left, crop_top, crop_right, crop_bottom))
        self.grab_release()
        self.destroy()
        self.callback(crop)

    def _cancel(self) -> None:
        self.grab_release()
        self.destroy()
        self.callback(None)


class LineSlider(tk.Canvas):
    """Minimal line-and-dot slider used for window opacity."""

    def __init__(self, parent, from_=45, to=100, value=94, command=None, **kwargs):
        super().__init__(parent, height=24, width=112, highlightthickness=0, bd=0, **kwargs)
        self.minimum = float(from_)
        self.maximum = float(to)
        self.value = float(value)
        self.command = command
        self.line_color = "#D4E0E8"
        self.accent_color = "#1686B8"
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Button-1>", self._move)
        self.bind("<B1-Motion>", self._move)

    def set_colors(self, background: str, line: str, accent: str) -> None:
        self.configure(bg=background)
        self.line_color = line
        self.accent_color = accent
        self._draw()

    def _x_for_value(self) -> float:
        width = max(24, self.winfo_width())
        return 10 + (width - 20) * (self.value - self.minimum) / (self.maximum - self.minimum)

    def _draw(self) -> None:
        self.delete("all")
        width = max(24, self.winfo_width())
        y = max(10, self.winfo_height() / 2)
        knob_x = self._x_for_value()
        self.create_line(10, y, width - 10, y, fill=self.line_color, width=3, capstyle="round")
        self.create_line(10, y, knob_x, y, fill=self.accent_color, width=3, capstyle="round")
        self.create_oval(knob_x - 6, y - 6, knob_x + 6, y + 6, fill=self.accent_color, outline="")

    def _move(self, event) -> None:
        width = max(24, self.winfo_width())
        ratio = min(1.0, max(0.0, (event.x - 10) / max(1, width - 20)))
        self.value = self.minimum + ratio * (self.maximum - self.minimum)
        self._draw()
        if self.command:
            self.command(self.value)


class PriceHelperApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.ocr = PriceOCR()
        self.capture_active = False
        self.f8_was_down = False
        self.status_job = None

        root.title(APP_NAME)
        root.geometry("720x360")
        root.minsize(560, 290)
        root.attributes("-topmost", True)
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        self._position_top_center()

        self.current_var = tk.StringVar()
        self.target_var = tk.StringVar()
        self.discount_var = tk.StringVar(value="—")
        self.actual_var = tk.StringVar(value="输入当前价和目标价后自动计算")
        self.coupon_threshold_var = tk.StringVar()
        self.coupon_reduction_var = tk.StringVar()
        self.coupon_result_var = tk.StringVar(value="未设置满减券")
        self.status_var = tk.StringVar(value="就绪 · 按 F8 可随时框选屏幕价格")
        self.topmost_var = tk.BooleanVar(value=True)
        self.opacity_var = tk.IntVar(value=94)
        self.theme_var = tk.StringVar(value="经典蓝")
        self._last_ui_scale = 0.0

        self._configure_style()
        self._build_ui()
        self._apply_theme()
        self.current_var.trace_add("write", self._calculate)
        self.target_var.trace_add("write", self._calculate)
        self.coupon_threshold_var.trace_add("write", self._calculate)
        self.coupon_reduction_var.trace_add("write", self._calculate)
        self.root.bind("<Configure>", self._resize_ui)
        self.root.after(100, self._poll_f8)

    def _configure_style(self) -> None:
        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.font_small = tkfont.Font(family="Microsoft YaHei UI", size=9)
        self.font_base = tkfont.Font(family="Microsoft YaHei UI", size=10)
        self.font_title = tkfont.Font(family="Microsoft YaHei UI", size=14, weight="bold")
        self.font_input = tkfont.Font(family="Segoe UI", size=14)
        self.font_result = tkfont.Font(family="Segoe UI", size=25, weight="bold")
        self.font_button = tkfont.Font(family="Microsoft YaHei UI", size=9, weight="bold")

    def _build_ui(self) -> None:
        self.header = ttk.Frame(self.root, style="Header.TFrame", padding=(16, 11, 16, 9))
        self.header.pack(fill="x")
        ttk.Label(self.header, text=APP_NAME, style="Title.TLabel").pack(side="left")

        self.top_button = ttk.Button(self.header, text="✔ 置顶", style="Top.TButton", command=self._toggle_topmost)
        self.top_button.pack(side="right", padx=(10, 0))
        self.opacity_slider = LineSlider(self.header, value=self.opacity_var.get(), command=self._set_opacity)
        self.opacity_slider.pack(side="right", padx=(4, 0))
        ttk.Label(self.header, text="透明度", style="Header.TLabel").pack(side="right", padx=(12, 0))
        self.theme_box = ttk.Combobox(
            self.header, textvariable=self.theme_var, values=list(THEMES), state="readonly", width=8, style="Theme.TCombobox"
        )
        self.theme_box.pack(side="right", padx=(5, 0))
        self.theme_box.bind("<<ComboboxSelected>>", lambda _event: self._apply_theme())
        ttk.Label(self.header, text="皮肤", style="Header.TLabel").pack(side="right", padx=(12, 0))

        self.card = ttk.Frame(self.root, style="Card.TFrame", padding=16)
        self.card.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        self.card.columnconfigure(0, weight=3, uniform="price")
        self.card.columnconfigure(1, weight=3, uniform="price")
        self.card.columnconfigure(2, weight=3, uniform="price")
        self.card.rowconfigure(5, weight=1)

        ttk.Label(self.card, text="当前价格", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(self.card, text="目标到手价（含券）", style="Card.TLabel").grid(row=0, column=1, sticky="w", padx=(14, 0))
        ttk.Label(self.card, text="应填降幅", style="Card.TLabel").grid(row=0, column=2, sticky="w", padx=(18, 0))

        current = ttk.Entry(self.card, textvariable=self.current_var, style="Price.TEntry", width=15)
        current.grid(row=1, column=0, sticky="ew", pady=(6, 5))
        self.target_entry = ttk.Entry(self.card, textvariable=self.target_var, style="Price.TEntry", width=15)
        self.target_entry.grid(row=1, column=1, sticky="ew", padx=(14, 0), pady=(6, 5))
        result_frame = ttk.Frame(self.card, style="Card.TFrame")
        result_frame.grid(row=1, column=2, rowspan=2, sticky="nsew", padx=(16, 0))
        ttk.Label(result_frame, textvariable=self.discount_var, style="Result.TLabel").pack(anchor="w")
        ttk.Button(result_frame, text="复制降幅", style="Soft.TButton", command=self._copy_discount).pack(anchor="w", pady=(2, 0))

        ttk.Button(self.card, text="框选取价  F8", style="Accent.TButton", command=self.begin_capture).grid(row=2, column=0, sticky="ew", pady=(5, 7))
        ttk.Button(self.card, text="清空", style="Soft.TButton", command=self._clear).grid(row=2, column=1, sticky="ew", padx=(14, 0), pady=(5, 7))
        ttk.Separator(self.card, orient="horizontal").grid(row=3, column=0, columnspan=3, sticky="ew", pady=(9, 11))

        coupon = ttk.Frame(self.card, style="Card.TFrame")
        coupon.grid(row=4, column=0, columnspan=2, sticky="ew")
        coupon.columnconfigure(1, weight=1)
        coupon.columnconfigure(3, weight=1)
        ttk.Label(coupon, text="满", style="Card.TLabel").grid(row=0, column=0, padx=(0, 6))
        ttk.Entry(coupon, textvariable=self.coupon_threshold_var, style="Coupon.TEntry", width=10).grid(row=0, column=1, sticky="ew")
        ttk.Label(coupon, text="减", style="Card.TLabel").grid(row=0, column=2, padx=7)
        ttk.Entry(coupon, textvariable=self.coupon_reduction_var, style="Coupon.TEntry", width=10).grid(row=0, column=3, sticky="ew")
        self.coupon_result_label = ttk.Label(self.card, textvariable=self.coupon_result_var, style="Hint.TLabel", justify="left")
        self.coupon_result_label.grid(row=4, column=2, sticky="nw", padx=(16, 0))

        self.actual_label = ttk.Label(self.card, textvariable=self.actual_var, style="Hint.TLabel", justify="left")
        self.actual_label.grid(row=5, column=0, columnspan=3, sticky="nw", pady=(11, 0))

        self.status_label = ttk.Label(self.root, textvariable=self.status_var, anchor="w", style="Status.TLabel")
        self.status_label.pack(fill="x", padx=17, pady=(0, 9))
        current.focus_set()

    def _position_top_center(self) -> None:
        self.root.update_idletasks()
        width = 720
        self.root.geometry(f"{width}x360+{max(0, (self.root.winfo_screenwidth() - width) // 2)}+18")

    def _apply_theme(self) -> None:
        colors = THEMES[self.theme_var.get()]
        bg, card = colors["bg"], colors["card"]
        text_color, muted, accent = colors["text"], colors["muted"], colors["accent"]
        self.root.configure(bg=bg)
        self.style.configure("TFrame", background=bg)
        self.style.configure("Header.TFrame", background=bg)
        self.style.configure("Card.TFrame", background=card)
        self.style.configure("TLabel", background=bg, foreground=text_color, font=self.font_base)
        self.style.configure("Header.TLabel", background=bg, foreground=muted, font=self.font_small)
        self.style.configure("Status.TLabel", background=bg, foreground=muted, font=self.font_small)
        self.style.configure("Card.TLabel", background=card, foreground=text_color, font=self.font_base)
        self.style.configure("Title.TLabel", background=bg, foreground=text_color, font=self.font_title)
        self.style.configure("Result.TLabel", background=card, foreground=accent, font=self.font_result)
        self.style.configure("Hint.TLabel", background=card, foreground=muted, font=self.font_small)
        self.style.configure(
            "Accent.TButton", font=self.font_button, foreground="#FFFFFF", background=accent,
            bordercolor=accent, lightcolor=accent, darkcolor=accent, padding=(12, 8), relief="flat"
        )
        self.style.map("Accent.TButton", background=[("active", colors["hover"])])
        self.style.configure(
            "Soft.TButton", font=self.font_base, foreground=text_color, background=colors["input"],
            bordercolor=colors["line"], lightcolor=colors["input"], darkcolor=colors["input"], padding=(10, 7)
        )
        self.style.map("Soft.TButton", background=[("active", colors["line"])])
        self.style.configure(
            "Top.TButton", font=self.font_small, foreground=text_color, background=bg,
            borderwidth=0, focuscolor=bg, padding=(5, 4), relief="flat"
        )
        self.style.map("Top.TButton", background=[("active", colors["line"])])
        self.style.configure(
            "Price.TEntry", font=self.font_input, foreground=text_color, fieldbackground=colors["input"],
            insertcolor=text_color, bordercolor=colors["line"], padding=(7, 6)
        )
        self.style.configure(
            "Coupon.TEntry", font=self.font_base, foreground=text_color, fieldbackground=colors["input"],
            insertcolor=text_color, bordercolor=colors["line"], padding=(6, 5)
        )
        self.style.configure(
            "Theme.TCombobox", font=self.font_small, foreground=text_color, fieldbackground=colors["input"],
            background=colors["input"], arrowcolor=text_color, bordercolor=colors["line"], padding=(5, 3)
        )
        self.style.map("Theme.TCombobox", fieldbackground=[("readonly", colors["input"])], foreground=[("readonly", text_color)])
        self.style.configure("TSeparator", background=colors["line"])
        self.opacity_slider.set_colors(bg, colors["line"], accent)

    def _resize_ui(self, event) -> None:
        if event.widget is not self.root:
            return
        scale = min(event.width / 720, event.height / 360)
        scale = max(0.82, min(1.5, scale))
        if abs(scale - self._last_ui_scale) < 0.025:
            return
        self._last_ui_scale = scale
        self.font_small.configure(size=max(8, round(9 * scale)))
        self.font_base.configure(size=max(9, round(10 * scale)))
        self.font_title.configure(size=max(12, round(14 * scale)))
        self.font_input.configure(size=max(12, round(14 * scale)))
        self.font_result.configure(size=max(20, round(25 * scale)))
        self.font_button.configure(size=max(8, round(9 * scale)))
        pad = max(10, round(16 * scale))
        self.card.configure(padding=pad)
        self.style.configure("Accent.TButton", padding=(round(12 * scale), round(8 * scale)))
        self.style.configure("Soft.TButton", padding=(round(10 * scale), round(7 * scale)))
        self.coupon_result_label.configure(wraplength=max(140, round(event.width * 0.29)))
        self.actual_label.configure(wraplength=max(450, event.width - 70))

    def _toggle_topmost(self) -> None:
        self.topmost_var.set(not self.topmost_var.get())
        self.root.attributes("-topmost", self.topmost_var.get())
        self.top_button.configure(text="✔ 置顶" if self.topmost_var.get() else "□ 置顶")

    def _set_opacity(self, value=None) -> None:
        if value is not None:
            self.opacity_var.set(round(float(value)))
        self.root.attributes("-alpha", max(0.45, self.opacity_var.get() / 100))

    def _calculate(self, *_args) -> None:
        current = decimal_or_none(self.current_var.get())
        target = decimal_or_none(self.target_var.get())
        if current is None or target is None:
            self.discount_var.set("—")
            self.actual_var.set("输入当前价和目标到手价后自动反算")
            self.coupon_result_var.set("未设置满减券" if not self.coupon_threshold_var.get().strip() and not self.coupon_reduction_var.get().strip() else "等待有效价格")
            return
        if current <= 0:
            self.discount_var.set("—")
            self.actual_var.set("当前价必须大于 0")
            return
        if target < 0:
            self.discount_var.set("—")
            self.actual_var.set("目标到手价不能小于 0")
            return

        threshold_text = self.coupon_threshold_var.get().strip()
        reduction_text = self.coupon_reduction_var.get().strip()
        activity_price = target
        coupon_applied = False

        if threshold_text or reduction_text:
            threshold = decimal_or_none(threshold_text)
            reduction = decimal_or_none(reduction_text)
            if threshold is None or reduction is None:
                self.discount_var.set("—")
                self.coupon_result_var.set("请完整填写“满”和“减”")
                self.actual_var.set("满减券信息不完整")
                return
            if threshold <= 0 or reduction <= 0 or reduction >= threshold:
                self.discount_var.set("—")
                self.coupon_result_var.set("满减金额设置不合理")
                self.actual_var.set("请检查满减券设置")
                return

            # The entered target is the final amount after the coupon, so the
            # platform activity price must be higher by the coupon reduction.
            coupon_activity_price = target + reduction
            if current < threshold:
                activity_price = target
                self.coupon_result_var.set(
                    f"当前价未满 {trim_decimal(threshold, 2)} · 已按无券计算"
                )
            elif coupon_activity_price < threshold:
                minimum_final = threshold - reduction
                activity_price = target
                self.coupon_result_var.set(
                    f"无法触发券（最低券后 {trim_decimal(minimum_final, 2)}）· 已按无券计算"
                )
            elif coupon_activity_price > current:
                activity_price = target
                self.coupon_result_var.set(
                    "券前活动价会高于当前价 · 已按无券计算"
                )
            else:
                activity_price = coupon_activity_price
                coupon_applied = True
                self.coupon_result_var.set(
                    f"需提报活动价 {trim_decimal(activity_price, 2)}　→　券后 {trim_decimal(target, 2)}"
                )
        else:
            self.coupon_result_var.set("未设置满减券 · 目标到手价即活动价")

        discount = (Decimal(1) - activity_price / current) * Decimal(100)
        if discount < 0:
            self.discount_var.set("—")
            self.actual_var.set("反算出的活动价高于当前价，无法通过降价实现")
            return
        display = trim_decimal(discount, 4)
        self.discount_var.set(f"{display}%")
        entered = trim_decimal(discount, 2)
        projected = current * (Decimal(1) - Decimal(entered) / Decimal(100))
        projected = projected.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        projected_final = projected
        if coupon_applied:
            projected_final = projected - decimal_or_none(reduction_text)
        self.actual_var.set(
            f"建议填：{entered}%　·　预计活动价：{projected}　·　预计到手：{projected_final.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}"
        )

    def _copy_discount(self) -> None:
        value = self.discount_var.get().replace("%", "")
        if value == "—":
            self._flash_status("请先输入有效价格")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self._flash_status(f"已复制降幅：{value}")

    def _clear(self) -> None:
        self.current_var.set("")
        self.target_var.set("")
        self.coupon_threshold_var.set("")
        self.coupon_reduction_var.set("")
        self._flash_status("已清空")

    def _poll_f8(self) -> None:
        try:
            is_down = bool(ctypes.windll.user32.GetAsyncKeyState(VK_F8) & 0x8000)
            if is_down and not self.f8_was_down and not self.capture_active:
                self.begin_capture()
            self.f8_was_down = is_down
        finally:
            self.root.after(80, self._poll_f8)

    def _virtual_screen(self) -> tuple[int, int, int, int]:
        user32 = ctypes.windll.user32
        return (
            user32.GetSystemMetrics(76),
            user32.GetSystemMetrics(77),
            user32.GetSystemMetrics(78),
            user32.GetSystemMetrics(79),
        )

    def begin_capture(self) -> None:
        if self.capture_active:
            return
        self.capture_active = True
        self.status_var.set("准备截图…")
        self.root.withdraw()
        self.root.after(220, self._show_capture_overlay)

    def _show_capture_overlay(self) -> None:
        try:
            bounds = self._virtual_screen()
            screenshot = ImageGrab.grab(all_screens=True)
            CaptureOverlay(self.root, screenshot, bounds, self._capture_finished)
        except Exception as exc:
            self.capture_active = False
            self.root.deiconify()
            messagebox.showerror(APP_NAME, f"无法截取屏幕：{exc}")

    def _capture_finished(self, crop: Image.Image | None) -> None:
        self.root.deiconify()
        self.root.lift()
        if crop is None:
            self.capture_active = False
            self._flash_status("已取消取价")
            return
        self.status_var.set("正在识别价格，首次使用可能需要几秒…")
        threading.Thread(target=self._recognize_worker, args=(crop,), daemon=True).start()

    def _recognize_worker(self, crop: Image.Image) -> None:
        try:
            value, detail = self.ocr.recognize(crop)
            self.root.after(0, self._recognize_done, value, detail)
        except Exception as exc:
            self.root.after(0, self._recognize_failed, str(exc))

    def _recognize_done(self, value: Decimal | None, detail: str) -> None:
        self.capture_active = False
        if value is None:
            self.status_var.set(detail + "；请缩小框选范围后重试，或手动输入")
            return
        self.current_var.set(trim_decimal(value, 2))
        self.status_var.set(f"取价成功：{trim_decimal(value, 2)}　({detail})")
        # Apply focus after the hidden window has been restored.
        self.root.after(50, self._focus_target_entry)

    def _focus_target_entry(self) -> None:
        self.root.lift()
        self.root.focus_force()
        self.target_entry.focus_set()
        self.target_entry.selection_range(0, tk.END)
        self.target_entry.icursor(tk.END)

    def _recognize_failed(self, error: str) -> None:
        self.capture_active = False
        self.status_var.set("识别失败，可手动输入当前价")
        messagebox.showerror(APP_NAME, f"OCR 识别失败：\n{error}")

    def _flash_status(self, message: str) -> None:
        self.status_var.set(message)
        if self.status_job:
            self.root.after_cancel(self.status_job)
        self.status_job = self.root.after(3500, lambda: self.status_var.set("就绪 · 按 F8 可随时框选屏幕价格"))


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("本工具目前仅支持 Windows。")
    enable_high_dpi()
    root = tk.Tk()
    PriceHelperApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
