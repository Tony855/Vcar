import yaml
from pathlib import Path
class Translator:
    def __init__(self):
        self.data = {}
        for f in Path(__file__).parent.parent.joinpath("locales").glob("*.yaml"):
            with open(f, "r", encoding="utf-8") as fp: self.data[f.stem] = yaml.safe_load(fp)
    def t(self, key, locale="zh", **vars):
        val = self.data.get(locale, {})
        for k in key.split("."): val = val.get(k, {}) if isinstance(val, dict) else str(val)
        if not isinstance(val, str): val = key
        for k, v in vars.items(): val = val.replace(f"{{{k}}}", str(v))
        return val
translator = Translator()
