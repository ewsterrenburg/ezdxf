# Copyright (c) 2019-2021 Manfred Moitzi
# License: MIT License
from typing import TYPE_CHECKING, Optional
import copy
from ezdxf.lldxf import validator
from ezdxf.math import NULLVEC
from ezdxf.lldxf.attributes import (
    DXFAttr,
    DXFAttributes,
    DefSubclass,
    XType,
    RETURN_DEFAULT,
    group_code_mapping,
)
from ezdxf.lldxf.const import DXF12, SUBCLASS_MARKER, DXF2010
from ezdxf.lldxf import const
from ezdxf.tools import set_flag_state
from ezdxf.tools.text import load_mtext_content, fast_plain_mtext, plain_mtext

from .dxfns import SubclassProcessor, DXFNamespace
from .dxfentity import base_class
from .dxfgfx import acdb_entity, elevation_to_z_axis
from .text import Text, acdb_text, acdb_text_group_codes
from .mtext import acdb_mtext_group_codes, MText
from .factory import register_entity

if TYPE_CHECKING:
    from ezdxf.eztypes import TagWriter, Tags, DXFEntity

__all__ = ["AttDef", "Attrib", "copy_attrib_as_text"]

# DXF Reference for ATTRIB is a total mess and incorrect, the AcDbText subclass
# for the ATTRIB entity is the same as for the TEXT entity, but the valign field
# from the 2nd AcDbText subclass of the TEXT entity is stored in the
# AcDbAttribute subclass:
attrib_fields = {
    # Version number: 0 = 2010
    "version": DXFAttr(280, default=0, dxfversion=DXF2010),
    # Tag string (cannot contain spaces):
    "tag": DXFAttr(
        2,
        default="",
        validator=validator.is_valid_attrib_tag,
        fixer=validator.fix_attrib_tag,
    ),
    # 1 = Attribute is invisible (does not appear)
    # 2 = This is a constant attribute
    # 4 = Verification is required on input of this attribute
    # 8 = Attribute is preset (no prompt during insertion)
    "flags": DXFAttr(70, default=0),
    # Field length (optional) (not currently used)
    "field_length": DXFAttr(73, default=0, optional=True),
    # Vertical text justification type (optional); see group code 73 in TEXT
    "valign": DXFAttr(
        74,
        default=0,
        optional=True,
        validator=validator.is_in_integer_range(0, 4),
        fixer=RETURN_DEFAULT,
    ),
    # Lock position flag. Locks the position of the attribute within the block
    # reference, example of double use of group codes in one sub class
    "lock_position": DXFAttr(
        280,
        default=0,
        dxfversion=DXF2010,
        optional=True,
        validator=validator.is_integer_bool,
        fixer=RETURN_DEFAULT,
    ),
}

# ATTDEF has an additional field: 'prompt'
# DXF attribute definitions are immutable, a shallow copy is sufficient:
attdef_fields = dict(attrib_fields)
attdef_fields["prompt"] = DXFAttr(
    3,
    default="",
    validator=validator.is_valid_one_line_text,
    fixer=validator.fix_one_line_text,
)

acdb_attdef = DefSubclass("AcDbAttributeDefinition", attdef_fields)
acdb_attdef_group_codes = group_code_mapping(acdb_attdef)
acdb_attrib = DefSubclass("AcDbAttribute", attrib_fields)
acdb_attrib_group_codes = group_code_mapping(acdb_attrib)

# For XRECORD the tag order is important and group codes appear multiple times,
# therefore this attribute definition needs a special treatment!
acdb_attdef_xrecord = DefSubclass(
    "AcDbXrecord",
    [
        # Duplicate record cloning flag (determines how to merge duplicate entries):
        # 1 = Keep existing
        ("cloning", DXFAttr(280, default=1)),
        # MText flag:
        # 2 = multiline attribute
        # 4 = constant multiline attribute definition
        ("mtext_flag", DXFAttr(70, default=0)),
        # isReallyLocked flag:
        #     0 = unlocked
        #     1 = locked
        (
            "really_locked",
            DXFAttr(
                70,
                default=0,
                validator=validator.is_integer_bool,
                fixer=RETURN_DEFAULT,
            ),
        ),
        # Number of secondary attributes or attribute definitions:
        ("secondary_attribs_count", DXFAttr(70, default=0)),
        # Hard-pointer id of secondary attribute(s) or attribute definition(s):
        ("secondary_attribs_handle", DXFAttr(70, default=0)),
        # Alignment point of attribute or attribute definition:
        ("align_point", DXFAttr(10, xtype=XType.point3d, default=NULLVEC)),
        ("current_annotation_scale", DXFAttr(40, default=0)),
        # attribute or attribute definition tag string
        (
            "tag",
            DXFAttr(
                2,
                default="",
                validator=validator.is_valid_attrib_tag,
                fixer=validator.fix_attrib_tag,
            ),
        ),
    ],
)


# Just for documentation:
# The "attached" MTEXT feature most likely does not exist!
#
#   A special MTEXT entity can follow the ATTDEF and ATTRIB entity, which starts
#   as a usual DXF entity with (0, 'MTEXT'), so processing can't be done here,
#   because for ezdxf is this a separated Entity.
#
#   The attached MTEXT entity: owner is None and handle is None
#   Linked as attribute `attached_mtext`.
#   I don't have seen this combination of entities in real world examples and is
#   ignored by ezdxf for now.
#
# No DXF files available which uses this feature - misleading DXF Reference!?

# Attrib and Attdef can have embedded MTEXT entities located in the
# <Embedded Object> subclass, see issue #258


class BaseAttrib(Text):
    XRECORD_DEF = acdb_attdef_xrecord

    def __init__(self):
        super().__init__()
        # Does subclass AcDbXrecord really exist?
        self.xrecord: Optional["Tags"] = None
        self.embedded_mtext: Optional["EmbeddedMText"] = None

    def _copy_data(self, entity: "DXFEntity") -> None:
        """Copy entity data, xrecord data and embedded MTEXT are not stored
        in the entity database.

        """
        assert isinstance(entity, BaseAttrib)
        entity.xrecord = copy.deepcopy(self.xrecord)
        entity.embedded_mtext = copy.deepcopy(self.embedded_mtext)

    def load_embedded_mtext(self, processor: SubclassProcessor) -> None:
        if not processor.embedded_objects:
            return
        embedded_object = processor.embedded_objects[0]
        if embedded_object:
            mtext = EmbeddedMText()
            mtext.load_dxf_tags(processor, embedded_object)
            self.embedded_mtext = mtext

    @property
    def is_const(self) -> bool:
        """This is a constant attribute."""
        return bool(self.dxf.flags & const.ATTRIB_CONST)

    @is_const.setter
    def is_const(self, state: bool) -> None:
        """This is a constant attribute."""
        self.dxf.flags = set_flag_state(
            self.dxf.flags, const.ATTRIB_CONST, state
        )

    @property
    def is_invisible(self) -> bool:
        """Attribute is invisible (does not appear)."""
        return bool(self.dxf.flags & const.ATTRIB_INVISIBLE)

    @is_invisible.setter
    def is_invisible(self, state: bool) -> None:
        """Attribute is invisible (does not appear)."""
        self.dxf.flags = set_flag_state(
            self.dxf.flags, const.ATTRIB_INVISIBLE, state
        )

    @property
    def is_verify(self) -> bool:
        """Verification is required on input of this attribute.
        (CAD application feature)

        """
        return bool(self.dxf.flags & const.ATTRIB_VERIFY)

    @is_verify.setter
    def is_verify(self, state: bool) -> None:
        """Verification is required on input of this attribute.
        (CAD application feature)

        """
        self.dxf.flags = set_flag_state(
            self.dxf.flags, const.ATTRIB_VERIFY, state
        )

    @property
    def is_preset(self) -> bool:
        """No prompt during insertion. (CAD application feature)"""
        return bool(self.dxf.flags & const.ATTRIB_IS_PRESET)

    @is_preset.setter
    def is_preset(self, state: bool) -> None:
        """No prompt during insertion. (CAD application feature)"""
        self.dxf.flags = set_flag_state(
            self.dxf.flags, const.ATTRIB_IS_PRESET, state
        )

    @property
    def has_embedded_mtext_entity(self) -> bool:
        """Returns ``True`` if the entity has an embedded MTEXT entity for multi
        line support.

        """
        return bool(self.embedded_mtext)

    def virtual_mtext_entity(self) -> MText:
        """Returns the embedded MTEXT entity as regular but virtual MTEXT
        entity with the same same graphical attributes as the
        host entity.
        """
        if not self.embedded_mtext:
            raise TypeError("no embedded MTEXT entity exist")
        mtext = self.embedded_mtext.virtual_mtext_entity()
        mtext.update_dxf_attribs(self.graphic_properties())
        return mtext

    def plain_mtext(self, fast=True) -> str:
        """Returns MText content without formatting codes. Returns an empty
        string "" if no embedded MTEXT entity exist.

        """
        if self.embedded_mtext:
            text = self.embedded_mtext.text
            if fast:
                return fast_plain_mtext(text, split=False)
            else:
                return plain_mtext(text, split=False)
        return ""


@register_entity
class AttDef(BaseAttrib):
    """DXF ATTDEF entity"""

    DXFTYPE = "ATTDEF"
    # Don't add acdb_attdef_xrecord here:
    DXFATTRIBS = DXFAttributes(base_class, acdb_entity, acdb_text, acdb_attdef)

    def load_dxf_attribs(
        self, processor: SubclassProcessor = None
    ) -> "DXFNamespace":
        dxf = super(Text, self).load_dxf_attribs(processor)
        # Do not call Text loader.
        if processor:
            processor.fast_load_dxfattribs(
                dxf, acdb_text_group_codes, 2, recover=True
            )
            processor.fast_load_dxfattribs(
                dxf, acdb_attdef_group_codes, 3, recover=True
            )
            self.xrecord = processor.find_subclass(self.XRECORD_DEF.name)
            self.load_embedded_mtext(processor)
            if processor.r12:
                # Transform elevation attribute from R11 to z-axis values:
                elevation_to_z_axis(dxf, ("insert", "align_point"))

        return dxf

    def export_entity(self, tagwriter: "TagWriter") -> None:
        # Text() writes 2x AcDbText which is not suitable for AttDef()
        self.export_acdb_entity(tagwriter)
        self.export_acdb_text(tagwriter)
        self.export_acdb_attdef(tagwriter)
        if self.xrecord:
            tagwriter.write_tags(self.xrecord)

    def export_acdb_attdef(self, tagwriter: "TagWriter") -> None:
        if tagwriter.dxfversion > DXF12:
            tagwriter.write_tag2(SUBCLASS_MARKER, acdb_attdef.name)
        self.dxf.export_dxf_attribs(
            tagwriter,
            [
                "version",
                "prompt",
                "tag",
                "flags",
                "field_length",
                "valign",
                "lock_position",
            ],
        )


@register_entity
class Attrib(BaseAttrib):
    """DXF ATTRIB entity"""

    DXFTYPE = "ATTRIB"
    # Don't add acdb_attdef_xrecord here:
    DXFATTRIBS = DXFAttributes(base_class, acdb_entity, acdb_text, acdb_attrib)

    def load_dxf_attribs(
        self, processor: SubclassProcessor = None
    ) -> "DXFNamespace":
        dxf = super(Text, self).load_dxf_attribs(processor)
        # Do not call Text loader.
        if processor:
            processor.fast_load_dxfattribs(
                dxf, acdb_text_group_codes, 2, recover=True
            )
            processor.fast_load_dxfattribs(
                dxf, acdb_attrib_group_codes, 3, recover=True
            )
            self.xrecord = processor.find_subclass(self.XRECORD_DEF.name)
            self.load_embedded_mtext(processor)
            if processor.r12:
                # Transform elevation attribute from R11 to z-axis values:
                elevation_to_z_axis(dxf, ("insert", "align_point"))
        return dxf

    def export_entity(self, tagwriter: "TagWriter") -> None:
        # Text() writes 2x AcDbText which is not suitable for AttDef()
        self.export_acdb_entity(tagwriter)
        self.export_acdb_attrib_text(tagwriter)
        self.export_acdb_attrib(tagwriter)
        if self.xrecord:
            tagwriter.write_tags(self.xrecord)

    def export_acdb_attrib_text(self, tagwriter: "TagWriter") -> None:
        # Despite the similarities to TEXT, it is different to
        # Text.export_acdb_text():
        if tagwriter.dxfversion > DXF12:
            tagwriter.write_tag2(SUBCLASS_MARKER, acdb_text.name)
        self.dxf.export_dxf_attribs(
            tagwriter,
            [
                "insert",
                "height",
                "text",
                "thickness",
                "rotation",
                "oblique",
                "style",
                "width",
                "halign",
                "align_point",
                "text_generation_flag",
                "extrusion",
            ],
        )

    def export_acdb_attrib(self, tagwriter: "TagWriter") -> None:
        if tagwriter.dxfversion > DXF12:
            tagwriter.write_tag2(SUBCLASS_MARKER, acdb_attrib.name)
        self.dxf.export_dxf_attribs(
            tagwriter,
            [
                "version",
                "tag",
                "flags",
                "field_length",
                "valign",
                "lock_position",
            ],
        )


IGNORE_FROM_ATTRIB = {
    "handle",
    "owner",
    "version",
    "prompt",
    "tag",
    "flags",
    "field_length",
    "lock_position",
}


def copy_attrib_as_text(attrib: BaseAttrib):
    """Returns the content of the ATTRIB/ATTDEF entity as a new virtual TEXT
    entity.

    """
    # TODO: MTEXT feature of DXF R2018+ is not supported yet!
    dxfattribs = attrib.dxfattribs(drop=IGNORE_FROM_ATTRIB)
    return Text.new(dxfattribs=dxfattribs, doc=attrib.doc)


class EmbeddedMText:
    """Representation of the embedded MTEXT object in ATTRIB and ATTDEF.

    Introduced in DXF R2018? The DXF reference of the `MTEXT`_ entity
    documents only the attached MTEXT entity. The ODA DWG specs includes all
    MTEXT attributes of MTEXT starting at group code 10

    Stores the required parameters to be shown as as MTEXT.
    The AcDbText subclass contains  the first line of the embedded MTEXT as
    plain text content as group code 1, but this tag seems not to be maintained
    if the ATTRIB entity is copied.

    Some DXF attributes are duplicated and maintained by the CAD application:

        - textstyle: same group code 7 (AcDbText, EmbeddedObject)
        - text (char) height: same group code 40 (AcDbText, EmbeddedObject)

    .. _MTEXT: https://help.autodesk.com/view/OARX/2018/ENU/?guid=GUID-7DD8B495-C3F8-48CD-A766-14F9D7D0DD9B

    """

    def __init__(self):
        # Attribute "dxf" contains the DXF attributes defined in subclass
        # "AcDbMText"
        self.dxf = DXFNamespace()
        self.text: str = ""

    def copy(self) -> "EmbeddedMText":
        copy_ = EmbeddedMText()
        copy_.dxf = copy.deepcopy(self.dxf)
        return copy_

    __copy__ = copy

    def load_dxf_tags(self, processor: SubclassProcessor, tags: "Tags") -> None:
        processor.fast_load_dxfattribs(
            self.dxf,
            group_code_mapping=acdb_mtext_group_codes,
            subclass=tags,
            recover=False,
        )
        self.text = load_mtext_content(tags)

    def virtual_mtext_entity(self) -> MText:
        """Returns the embedded MTEXT entity as regular but virtual MTEXT
        entity. This entity does not have the graphical attributes of the host
        entity (ATTRIB/ATTDEF).

        """
        mtext = MText.new(dxfattribs=self.dxf.all_existing_dxf_attribs())
        mtext.text = self.text
        return mtext
