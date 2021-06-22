from typing import Tuple, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from ezdxf.eztypes import DXFGraphic

LayerName = str
Color = str
Radians = float
RGB = Tuple[int, int, int]
FilterFunc = Callable[["DXFGraphic"], bool]
