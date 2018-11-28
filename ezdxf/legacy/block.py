# Created: 25.03.2011
# Copyright (c) 2011-2018, Manfred Moitzi
# License: MIT License
from ezdxf.lldxf.const import LAYOUT_NAMES

from .graphics import GraphicEntity, ExtendedTags, make_attribs, DXFAttr, DXFAttributes, DefSubclass

_BLOCK_TPL = """0
BLOCK
5
0
8
0
2
BLOCKNAME
3
BLOCKNAME
70
0
10
0.0
20
0.0
30
0.0
1

"""


class Block(GraphicEntity):
    __slots__ = ()
    TEMPLATE = ExtendedTags.from_text(_BLOCK_TPL)
    DXFATTRIBS = make_attribs({
        'name': DXFAttr(2),
        'name2': DXFAttr(3),
        'flags': DXFAttr(70),
        'base_point': DXFAttr(10, xtype='Point2D/3D'),
        'xref_path': DXFAttr(1),
    })
    # block entity flags
    # This is an anonymous block generated by hatching, associative dimensioning,
    # other internal operations, or an application
    ANONYMOUS = 1

    # This block has non-constant attribute definitions (this bit is not set if the block has
    # any attribute definitions that are constant, or has no attribute definitions at all)
    NON_CONSTANT_ATTRIBUTES = 2
    XREF = 4  # This block is an external reference (xref)
    XREF_OVERLAY = 8  # This block is an xref overlay
    EXTERNAL = 16  # This block is externally dependent
    RESOLVED = 32  # This is a resolved external reference, or dependent of an external reference (ignored on input)
    REFERENCED = 64  # This definition is a referenced external reference (ignored on input)

    @property
    def is_layout_block(self):
        """
        True if block is a model space or paper space block definition.

        """
        name = self.dxf.name.lower()
        return any(name.startswith(layout_name) for layout_name in LAYOUT_NAMES)


class EndBlk(GraphicEntity):
    __slots__ = ()
    TEMPLATE = ExtendedTags.from_text("  0\nENDBLK\n  5\n0\n")
    DXFATTRIBS = DXFAttributes(DefSubclass(None, {'handle': DXFAttr(5)}))
