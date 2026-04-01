# v5 프록시: 실제 코드는 1_director/bitcoin_data.py
import importlib as _il
_m = _il.import_module("librarian.1_director.bitcoin_data")

get_prompt_block = _m.get_prompt_block
get_news = _m.get_news
get_weather_for = _m.get_weather_for
start_background_update = _m.start_background_update
