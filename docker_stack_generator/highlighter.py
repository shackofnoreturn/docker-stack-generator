"""
Jinja2/YAML syntax highlighter for QPlainTextEdit / QTextEdit.
Highlights:
  - Jinja2 blocks  {% ... %}  {{...}}  {#...#}
  - YAML keys
  - YAML strings / values
  - Comments (#)
  - Shell-style variables  ${VAR}
"""
from __future__ import annotations

from PyQt6.QtCore import QRegularExpression, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
)


def _fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    if italic:
        f.setFontItalic(True)
    return f


class Jinja2YamlHighlighter(QSyntaxHighlighter):
    """Highlights Jinja2 template syntax embedded in YAML."""

    def __init__(self, document):
        super().__init__(document)

        # Each entry: (pattern, format, capture_group)
        self._rules: list[tuple[QRegularExpression, QTextCharFormat, int]] = []

        # 1. YAML value after colon (base value color; more specific rules override below)
        self._rules.append((
            QRegularExpression(r":\s+([^\n#]+)"),
            _fmt("#98c379"),
            1,
        ))
        # 2. YAML key (word followed by colon, including after list dash)
        self._rules.append((
            QRegularExpression(r"^\s*([A-Za-z_][A-Za-z0-9_\-]*)(?=\s*:)"),
            _fmt("#61afef", bold=True),
            1,
        ))
        self._rules.append((
            QRegularExpression(r"^\s*-\s+([A-Za-z_][A-Za-z0-9_\-]*)(?=\s*:)"),
            _fmt("#61afef", bold=True),
            1,
        ))
        # 3. YAML list indicator  -
        self._rules.append((
            QRegularExpression(r"^\s*(-)\s"),
            _fmt("#56b6c2", bold=True),
            1,
        ))
        # 4. Quoted strings
        self._rules.append((
            QRegularExpression(r"'[^']*'"),
            _fmt("#98c379"),
            0,
        ))
        self._rules.append((
            QRegularExpression(r'"[^"]*"'),
            _fmt("#98c379"),
            0,
        ))
        # 5. Shell variable  ${VAR} (overrides string/value color so vars pop)
        self._rules.append((
            QRegularExpression(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}"),
            _fmt("#e5c07b", bold=True),
            0,
        ))
        # 6. Jinja2 expression  {{ ... }}
        self._rules.append((
            QRegularExpression(r"\{\{.*?\}\}"),
            _fmt("#e06c75", bold=True),
            0,
        ))
        # 7. Jinja2 block  {% ... %}
        self._rules.append((
            QRegularExpression(r"\{%.*?%\}"),
            _fmt("#c678dd", bold=True),
            0,
        ))
        # 8. Comments (# and {# ... #}) last so they win over everything
        self._rules.append((
            QRegularExpression(r"#[^\n]*"),
            _fmt("#7f848e", italic=True),
            0,
        ))
        self._rules.append((
            QRegularExpression(r"\{#.*?#\}"),
            _fmt("#7f848e", italic=True),
            0,
        ))

    def highlightBlock(self, text: str):
        for pattern, fmt, group in self._rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                match = it.next()
                self.setFormat(match.capturedStart(group), match.capturedLength(group), fmt)
