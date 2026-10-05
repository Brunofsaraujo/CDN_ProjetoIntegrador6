"""
compat.py — Ajustes de ambiente aplicados ANTES de importar scikit-learn

pyarrow bloqueado pelo Windows
------------------------------
O scikit-learn 1.8 tenta `import pyarrow` ao ser importado e só tolera a
ausência do pacote (ModuleNotFoundError). Em máquinas com Controle de
Aplicativo do Windows (Smart App Control / WDAC), o pyarrow está instalado mas
a DLL é bloqueada, e o import levanta ImportError — que o scikit-learn não
captura. Resultado: o .pkl não carrega e o site exibe métricas zeradas.

O site não usa pyarrow. Quando o import falha por bloqueio, este módulo faz o
Python tratá-lo como ausente, que é o caso que o scikit-learn sabe lidar.
Em ambientes onde o pyarrow funciona (Linux, PythonAnywhere), nada muda.

Importe este módulo antes de qualquer `import sklearn` ou `pickle.load`.
"""

import importlib.abc
import sys

PYARROW_BLOQUEADO = False
MOTIVO = ""


class _PyarrowAusente(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name == "pyarrow" or name.startswith("pyarrow."):
            raise ModuleNotFoundError(f"pyarrow indisponível neste ambiente: {MOTIVO}")
        return None


def _aplicar() -> None:
    global PYARROW_BLOQUEADO, MOTIVO
    if any(isinstance(f, _PyarrowAusente) for f in sys.meta_path):
        return
    try:
        import pyarrow  # noqa: F401
    except ModuleNotFoundError:
        return  # não instalado: o scikit-learn já lida com isso
    except ImportError as e:  # instalado, mas a DLL foi bloqueada
        PYARROW_BLOQUEADO, MOTIVO = True, str(e)
        for nome in [m for m in sys.modules if m == "pyarrow" or m.startswith("pyarrow.")]:
            del sys.modules[nome]
        sys.meta_path.insert(0, _PyarrowAusente())


_aplicar()
