# -*- coding: utf-8 -*-
"""UI 主题令牌与 ttk 样式配置。"""

# ---- 配色令牌 ----
C_BG = "#F5F6F7"           # 界面主背景
C_CARD = "#FFFFFF"         # 卡片面板底色
C_BORDER = "#E3E6EA"      # 分隔线
C_TEXT = "#1F2329"         # 主文字
C_SUBTEXT = "#8A919F"     # 次级文字
C_GREEN = "#07C160"       # 微信绿：启动/运行中
C_GREEN_DK = "#059B4C"    # 启动按钮 hover/按下
C_GRAY = "#B7BDC7"       # 停止/未运行
C_RED = "#FA5151"         # 错误/失败
C_ACCENT_BG = "#E8F7EF"   # 绿色状态底色
C_ACCENT_BG_RED = "#FDECEC"  # 红色状态底色

# ---- 字体令牌 ----
FONT = ("Microsoft YaHei UI", 10)
FONT_BOLD = ("Microsoft YaHei UI", 10, "bold")
FONT_TITLE = ("Microsoft YaHei UI", 12, "bold")
FONT_TAG = ("Microsoft YaHei UI", 9)
FONT_MONO = ("Consolas", 10)


def configure_style(root):
    """应用自定义 ttk 主题样式，让界面在新旧 Windows 上保持一致观感。"""
    try:
        import tkinter.font as tkfont

        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            try:
                tkfont.nametofont(name).configure(family="Microsoft YaHei UI", size=10)
            except Exception:
                pass
    except Exception:
        pass

    try:
        from tkinter import ttk

        style = ttk.Style(root)
        for theme in ("vista", "xpnative"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        style.configure("TFrame", background=C_BG)
        style.configure("TLabel", background=C_BG, foreground=C_TEXT, font=FONT)
        style.configure("TEntry", padding=5, fieldbackground=C_CARD, borderwidth=1)
        style.configure("TCheckbutton", background=C_BG, foreground=C_TEXT, font=FONT)
        style.configure(
            "TLabelframe", background=C_BG,
            bordercolor=C_BORDER, lightcolor=C_BORDER, darkcolor=C_BORDER,
        )
        style.configure("TLabelframe.Label", background=C_BG, foreground=C_SUBTEXT, font=FONT_BOLD)
        # 主按钮：绿色粗体文字 + 适度内边距，任何主题下都清晰可读
        style.configure("Accent.TButton", font=FONT_BOLD, foreground=C_GREEN, padding=(10, 4))
        style.configure("TButton", font=FONT, padding=(8, 4))
    except Exception:
        pass