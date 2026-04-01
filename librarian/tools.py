# v5 프록시: 1_director + 3_evaluator 도구를 합쳐서 제공
import importlib as _il
from google.genai import types as _types

_d = _il.import_module("librarian.1_director.tools")
_e = _il.import_module("librarian.3_evaluator.tools")

# Director
parse_url = _d.parse_url
normalize_url = _d.normalize_url
google_search_tool = _d.google_search_tool
director_tools = _d.director_tools
DIRECTOR_TOOL_NAMES = _d.DIRECTOR_TOOL_NAMES
execute_tool = _d.execute_tool

# Evaluator
evaluator_tools = _e.evaluator_tools
EVALUATOR_TOOL_NAMES = _e.EVALUATOR_TOOL_NAMES

# v4 호환: 전체 도구 합본
library_tools = [_types.Tool(function_declarations=_d.director_declarations + _e.evaluator_declarations)]
