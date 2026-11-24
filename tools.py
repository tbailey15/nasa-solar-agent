"""The four tools the agent can call.

Every tool takes a dict of arguments, checks them, and returns a dict.
Bad input raises ToolError, which the agent loop turns into an observation
so the model can try again.
"""

import ast
import operator

from retrieval import PLANETS, Retriever


class ToolError(Exception):
    pass


RETRIEVER = Retriever()


def search_docs(args):
    """Find passages in the NASA collection. Optional planet narrows the search to one page."""
    query = str(args.get("query", "")).strip()
    if not query:
        raise ToolError("search_docs needs a non-empty 'query'.")
    planet = args.get("planet")
    if planet:
        planet = str(planet).lower().strip()
        if planet not in PLANETS:
            raise ToolError(f"Unknown planet '{planet}'. Use one of: {', '.join(PLANETS)}.")
    hits = RETRIEVER.search(query, k=3, planet=planet)
    if not hits:
        return {"results": [], "note": "No matching passages in the NASA collection."}
    return {"results": [
        {"chunk_id": h["id"], "page": h["title"], "url": h["url"], "section": h["section"],
         "text": h["text"]}
        for h in hits]}


# only these operations are allowed in calculate
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg}


def _evaluate(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        return OPS[type(node.op)](_evaluate(node.left), _evaluate(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
        return OPS[type(node.op)](_evaluate(node.operand))
    raise ToolError("Only numbers and + - * / ** ( ) are allowed.")


def calculate(args):
    """Evaluate a plain arithmetic expression like '43440.7 * 2 / 74897'. Commas are removed."""
    expression = str(args.get("expression", "")).replace(",", "").strip()
    if not expression:
        raise ToolError("calculate needs an 'expression'.")
    try:
        tree = ast.parse(expression, mode="eval")
        value = _evaluate(tree.body)
    except ZeroDivisionError:
        raise ToolError("Division by zero.")
    except SyntaxError:
        raise ToolError(f"Could not read the expression '{expression}'.")
    return {"expression": expression, "result": round(value, 4)}


# factor to convert into a base unit for each kind of quantity
LINEAR_UNITS = {
    "miles": ("length", 1609.344), "kilometers": ("length", 1000.0), "meters": ("length", 1.0),
    "feet": ("length", 0.3048), "au": ("length", 149_597_870_700.0),
    "hours": ("time", 3600.0), "days": ("time", 86400.0), "years": ("time", 31_557_600.0),
    "minutes": ("time", 60.0),
}
ALIASES = {"mile": "miles", "mi": "miles", "km": "kilometers", "kilometer": "kilometers",
           "m": "meters", "meter": "meters", "foot": "feet", "ft": "feet",
           "astronomical units": "au", "astronomical unit": "au",
           "hour": "hours", "h": "hours", "day": "days", "year": "years", "minute": "minutes",
           "f": "fahrenheit", "°f": "fahrenheit", "c": "celsius", "°c": "celsius",
           "k": "kelvin", "kelvins": "kelvin"}
TEMPERATURE = {"fahrenheit", "celsius", "kelvin"}


def _unit(name):
    name = str(name).lower().strip()
    return ALIASES.get(name, name)


def _to_kelvin(value, unit):
    if unit == "celsius":
        return value + 273.15
    if unit == "fahrenheit":
        return (value - 32) * 5 / 9 + 273.15
    return value


def _from_kelvin(value, unit):
    if unit == "celsius":
        return value - 273.15
    if unit == "fahrenheit":
        return (value - 273.15) * 9 / 5 + 32
    return value


def convert_units(args):
    """Convert a value between length, time or temperature units."""
    try:
        value = float(str(args.get("value")).replace(",", ""))
    except (TypeError, ValueError):
        raise ToolError("convert_units needs a numeric 'value'.")
    source, target = _unit(args.get("from_unit", "")), _unit(args.get("to_unit", ""))
    if source in TEMPERATURE and target in TEMPERATURE:
        result = _from_kelvin(_to_kelvin(value, source), target)
    elif source in LINEAR_UNITS and target in LINEAR_UNITS:
        kind_a, factor_a = LINEAR_UNITS[source]
        kind_b, factor_b = LINEAR_UNITS[target]
        if kind_a != kind_b:
            raise ToolError(f"Cannot convert {kind_a} ({source}) to {kind_b} ({target}).")
        result = value * factor_a / factor_b
    else:
        known = sorted(set(LINEAR_UNITS) | TEMPERATURE)
        raise ToolError(f"Unknown unit pair '{source}' -> '{target}'. Known units: {', '.join(known)}.")
    return {"value": value, "from_unit": source, "to_unit": target, "result": round(result, 4)}


def read_chunk(args):
    """Return the full text of one chunk by id (ids come from search_docs results)."""
    chunk = RETRIEVER.get(str(args.get("chunk_id", "")).strip())
    if chunk is None:
        raise ToolError("Unknown chunk_id. Use an id returned by search_docs.")
    return {"chunk_id": chunk["id"], "page": chunk["title"], "url": chunk["url"],
            "section": chunk["section"], "text": chunk["text"]}


TOOLS = {
    "search_docs": (search_docs, 'search_docs {"query": str, "planet": optional one of mercury..neptune} '
                                 "- find passages in the NASA pages"),
    "read_chunk": (read_chunk, 'read_chunk {"chunk_id": str} - read one passage again by its id'),
    "calculate": (calculate, 'calculate {"expression": str} - arithmetic only, e.g. "86881.4 / 74897"'),
    "convert_units": (convert_units, 'convert_units {"value": number, "from_unit": str, "to_unit": str} '
                                     "- miles/kilometers/meters/feet/au, hours/days/years, fahrenheit/celsius/kelvin"),
}
