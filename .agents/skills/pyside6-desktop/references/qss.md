# QSS Styling — the bug class that causes invisible text/buttons

QSS (Qt Style Sheets) looks like CSS but behaves differently in ways that reliably produce "invisible text on Windows dark mode" bugs.

## Rule 1: Every inline `setStyleSheet` MUST include `color:`

When you call `setStyleSheet()` on a widget, Qt **overrides the global QSS** for that widget. The global `QLabel { color: #202124; }` no longer applies. On Windows dark mode, the inherited color can become white — invisible on a white background.

```python
# WRONG — text invisible on Windows dark mode
title.setStyleSheet("font-size: 20px; font-weight: bold;")

# RIGHT — explicit color always visible
title.setStyleSheet("font-size: 20px; font-weight: bold; color: #202124;")
```

Applies to QLabel, QCheckBox, QPushButton, and ANY widget with inline styles. If you set font-size, font-weight, font-family, or padding inline, you MUST also set color.

## Rule 2: Parent QFrame `background-color` bleeds to child widgets

When a QFrame has `setStyleSheet("background-color: #f8f9fa; ...")`, ALL child widgets inherit that background. QPushButtons inside the frame lose their blue background and get the frame's grey — white button text → invisible.

```python
# WRONG — button inside colored frame becomes invisible
frame.setStyleSheet("background-color: #f8f9fa; border: 1px solid #dadce0;")
btn = QPushButton("Connect")  # white text on grey bg = invisible
layout.addWidget(btn)

# RIGHT — give the button explicit styling
btn.setObjectName("btn_secondary")  # uses global QSS rule
# OR:
btn.setStyleSheet(
    "background-color: #1a73e8; color: white; border: none; "
    "border-radius: 4px; padding: 8px 24px; min-height: 36px;"
)
```

## Rule 3: QCheckBox indicators need explicit styling

OS default checkbox appearance varies wildly. On Windows dark mode, checkboxes can be invisible. Always style in the global QSS:

```css
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 2px solid #5f6368;
    border-radius: 3px;
    background-color: #ffffff;
}

QCheckBox::indicator:checked {
    background-color: #1a73e8;
    border-color: #1a73e8;
}

QCheckBox::indicator:hover {
    border-color: #1a73e8;
}
```

## Rule 4: QScrollArea viewport needs explicit background

Without it, scroll areas render with black/dark background on some platforms:

```css
QScrollArea {
    border: none;
    background-color: #ffffff;
}

QScrollArea > QWidget {
    background-color: #ffffff;
}
```

## Rule 5: Force light theme at application level

```css
QWidget {
    background-color: #ffffff;
    color: #202124;
}
```

This prevents OS dark mode from affecting the app. But inline `setStyleSheet` still overrides it — which is why Rule 1 exists.

## Rule 26: QSS cascade is NOT like CSS

Qt stylesheets do NOT auto-inherit font/color from parents like CSS does. When you set a stylesheet on a parent widget, child widgets don't inherit those properties — you must set them explicitly on each child.

```python
# WRONG — expecting CSS-like inheritance
parent.setStyleSheet("color: #202124; font-size: 14px;")
child_label = QLabel("text")  # does NOT inherit color from parent

# RIGHT — set on each widget
child_label.setStyleSheet("color: #202124; font-size: 14px;")
```

This is the root cause of most "invisible text" bugs. If in doubt, set `color:` on every widget.

## Rule 27: Intermediate `setStyleSheet()` creates a style firewall

When a QFrame or QWidget has `setStyleSheet()` called on it, ALL descendant widgets lose cascade access to grandparent stylesheets. The intermediate stylesheet creates a "firewall."

```python
# App-level QSS: QLabel { color: #202124; }

frame = QFrame()
frame.setStyleSheet("background-color: #f8f9fa;")  # creates firewall

label = QLabel("text")  # LOSES access to app-level QLabel rule
frame_layout.addWidget(label)
# label text color is now undefined — could be white on dark mode

# Fix: always set color on labels inside styled frames
label.setStyleSheet("color: #202124;")
```

This is why Rule 1 exists — Rule 27 explains the deeper mechanism. Any styled container breaks the cascade for all children.

## Rule 36: QComboBox dropdown needs separate QListView styling

The QComboBox dropdown is a separate QListView that doesn't inherit from the combo box's stylesheet. On dark mode, this causes white text on white background in dropdowns.

```css
/* Must style BOTH the combo box AND its dropdown */
QComboBox {
    background-color: #ffffff;
    color: #202124;
}

QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #202124;
    selection-background-color: #1a73e8;
    selection-color: #ffffff;
}
```
