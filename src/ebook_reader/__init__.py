r"""ebook-reader: a self-contained EPUB reader with careful CJK typesetting.

Layering (see arch/architecture.md section 4.2)::

    app     -> typeset -> domain -> stdlib
      \__________/
           |
        Qt6 platform

``domain`` must never import Qt (ADR-012).
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
