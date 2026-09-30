import sys
from unittest.mock import MagicMock

class DummyFinder:
    @classmethod
    def find_spec(cls, fullname, path, target=None):
        if fullname.split('.')[0] in ("aqt", "anki", "pypresence") or fullname == 'src.Ankimon.pyobj.translator':
            from importlib.machinery import ModuleSpec
            from importlib.abc import Loader

            class DummyLoader(Loader):
                def create_module(self, spec):
                    m = MagicMock()
                    m.__name__ = fullname
                    return m
                def exec_module(self, module):
                    pass
            return ModuleSpec(fullname, DummyLoader())
        return None

sys.meta_path.insert(0, DummyFinder)
