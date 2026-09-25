def normalize_cell(value):
    """Convierte una celda que llega de un Excel (vía JSON del frontend o
    CSV) a texto limpio. Un número entero puede llegar como `54654`,
    `54654.0` o `"54654.0"` según cómo lo leyó la herramienta; en los tres
    casos es el SKU/EAN `"54654"`. Vacío/`None` → `""`."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text
