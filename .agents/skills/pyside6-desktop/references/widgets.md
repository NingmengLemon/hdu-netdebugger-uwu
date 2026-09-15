# Widget Visibility Traps

Subtle UX bugs from how Qt parents/children and setVisible() interact.

## Rule 6: `setVisible(False)` on parent hides ALL children

If you hide a QWidget that contains buttons, the buttons become inaccessible to the user. They can't see or click them.

```python
# WRONG — hides the Load button too!
class WorkListWidget(QWidget):
    def _setup_ui(self):
        self._btn_load = QPushButton("Load CSV...")
        self._table = QTableWidget()
        self.setVisible(False)  # entire widget hidden, including button

# RIGHT — hide only the table, keep button visible
class WorkListWidget(QWidget):
    def _setup_ui(self):
        self._btn_load = QPushButton("Load CSV...")
        self._table = QTableWidget()
        self._table.setVisible(False)  # only table hidden, button accessible
```

## Rule 7: `setVisible` must be restored in BOTH reset() and re-init methods

If `_show_complete()` hides a label, both `reset()` and `start_*()` must show it again. Otherwise the widget stays hidden on the next cycle.

```python
def _show_complete(self):
    self._instruction.setVisible(False)  # hidden at completion

def start_identification(self, identifier):
    self._instruction.setVisible(True)   # MUST re-show

def reset(self):
    self._instruction.setVisible(True)   # MUST also re-show here
```

## Rule 8: `reset()` must restore ALL dynamic styles

If `load_result()` sets a title to green on success, `reset()` must restore the default color. Otherwise the next display shows stale green text.

```python
def load_result(self, result):
    if result.success:
        self._title.setStyleSheet(
            f"font-size: 20px; font-weight: bold; color: {COLORS['success']};"
        )

def reset(self):
    self._title.setStyleSheet(
        "font-size: 20px; font-weight: bold; color: #202124;"  # restore default
    )
```
