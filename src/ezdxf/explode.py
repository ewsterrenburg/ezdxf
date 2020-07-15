# Copyright (c) 2020, Manfred Moitzi
# License: MIT License
import logging
import math
from typing import TYPE_CHECKING, Iterable, Union, Callable, Optional, cast

from ezdxf.entities import factory
from ezdxf.lldxf.const import DXFStructureError, DXFTypeError, VERTEXNAMES, BYBLOCK, LINEWEIGHT_BYLAYER
from ezdxf.math import Vector, bulge_to_arc, OCS
from ezdxf.math.transformtools import NonUniformScalingError, InsertTransformationError
from ezdxf.query import EntityQuery

logger = logging.getLogger('ezdxf')

if TYPE_CHECKING:
    from ezdxf.eztypes import (
        Insert, BaseLayout, DXFGraphic, LWPolyline, Polyline, Attrib, Line, Arc, Face3d, Text,
        Leader,
    )


def explode_block_reference(block_ref: 'Insert', target_layout: 'BaseLayout') -> EntityQuery:
    """
    Explode a block reference into single DXF entities.

    Transforms the block entities into the required WCS location by applying the block reference
    attributes `insert`, `extrusion`, `rotation` and the scaling values `xscale`, `yscale` and `zscale`.
    Multiple inserts by row and column attributes is not supported.

    Returns an EntityQuery() container with all exploded DXF entities.

    Attached ATTRIB entities are converted to TEXT entities, this is the behavior of the BURST command of
    the AutoCAD Express Tools.

    Args:
        block_ref: Block reference entity (INSERT)
        target_layout: explicit target layout for exploded DXF entities

    .. warning::

        **Non uniform scaling** lead to incorrect results for text entities (TEXT, MTEXT, ATTRIB) and
        some other entities like ELLIPSE, SHAPE, HATCH with arc or ellipse path segments and
        POLYLINE/LWPOLYLINE with arc segments.

    (internal API)

    """
    if target_layout is None:
        raise DXFStructureError('Target layout is None.')

    if block_ref.doc is None:
        raise DXFStructureError('Block reference has to be assigned to a DXF document.')

    entitydb = block_ref.doc.entitydb
    assert entitydb is not None, 'Exploding a block reference requires an entity database.'

    dxffactory = block_ref.doc.dxffactory
    assert dxffactory is not None, 'Exploding a block reference requires a DXF entity factory.'

    entities = []

    for entity in virtual_block_reference_entities(block_ref):
        dxftype = entity.dxftype()
        entitydb.add(entity)
        target_layout.add_entity(entity)
        if dxftype == 'DIMENSION':
            # Render a graphical representation for each exploded DIMENSION entity as anonymous block.
            cast('Dimension', entity).render()
        entities.append(entity)

    # Convert attached ATTRIB entities to TEXT entities:
    # This is the behavior of the BURST command of the AutoCAD Express Tools
    for attrib in block_ref.attribs:
        # Attached ATTRIB entities are already located in the WCS
        text = attrib_to_text(attrib, dxffactory)
        target_layout.add_entity(text)
        entities.append(text)

    source_layout = block_ref.get_layout()
    if source_layout is not None:
        # Remove and destroy exploded INSERT if assigned to a layout
        source_layout.delete_entity(block_ref)
    else:
        entitydb.delete_entity(block_ref)
    return EntityQuery(entities)


IGNORE_FROM_ATTRIB = {'version', 'prompt', 'tag', 'flags', 'field_length', 'lock_position'}


def attrib_to_text(attrib: 'Attrib', dxffactory) -> 'Text':
    dxfattribs = attrib.dxfattribs(drop=IGNORE_FROM_ATTRIB)
    # ATTRIB has same owner as INSERT but does not reside in any EntitySpace() and must not deleted from any layout.
    dxffactory.doc.entitydb.delete_entity(attrib)
    # New TEXT entity has same handle as the deleted ATTRIB entity and replaces the ATTRIB entity in the database.
    return dxffactory.create_db_entry('TEXT', dxfattribs=dxfattribs)


def virtual_block_reference_entities(block_ref: 'Insert',
                                     skipped_entity_callback: Optional[Callable[['DXFGraphic', str], None]] = None
                                     ) -> Iterable['DXFGraphic']:
    """
    Yields 'virtual' parts of block reference `block_ref`. This method is meant to examine the the block reference
    entities without the need to explode the block reference. The `skipped_entity_callback()` will be called for all
    entities which are not processed, signature: :code:`skipped_entity_callback(entity: DXFEntity, reason: str)`,
    `entity` is the original (untransformed) DXF entity of the block definition, the `reason` string is an
    explanation why the entity was skipped.

    This entities are located at the 'exploded' positions, but are not stored in the entity database, have no handle
    and are not assigned to any layout.

    Args:
        block_ref: Block reference entity (INSERT)
        skipped_entity_callback: called whenever the transformation of an entity is not supported and so was skipped.

    .. warning::

        **Non uniform scaling** returns incorrect results for text entities (TEXT, MTEXT, ATTRIB) and
        some other entities like HATCH with arc or ellipse path segments.

    (internal API)

    """
    assert block_ref.dxftype() == 'INSERT'
    Ellipse = cast('Ellipse', factory.cls('ELLIPSE'))
    if skipped_entity_callback is None:
        def skipped_entity_callback(entity, reason):
            logger.debug(f'(Virtual Block Reference Entities) Ignoring {str(entity)}: "{reason}"')

    def disassemble(layout) -> Iterable['DXFGraphic']:
        for entity in layout:
            # Do not explode ATTDEF entities. Already available in Insert.attribs
            if entity.dxftype() == 'ATTDEF':
                continue

            try:
                copy = entity.copy()
            except DXFTypeError:
                skipped_entity_callback(entity, 'non copyable')
            else:
                if hasattr(copy, 'remove_association'):
                    copy.remove_association()
                yield copy

    def transform(entities):
        for entity in entities:
            try:
                entity.transform(m)
            except NotImplementedError:
                skipped_entity_callback(entity, 'non transformable')
            except NonUniformScalingError:
                dxftype = entity.dxftype()
                if dxftype in {'ARC', 'CIRCLE'}:
                    if entity.dxf.radius > 0:
                        yield Ellipse.from_arc(entity).transform(m)
                    else:
                        skipped_entity_callback(entity, f'Invalid radius in entity {str(entity)}.')
                elif dxftype in {'LWPOLYLINE', 'POLYLINE'}:  # has arcs
                    yield from transform(entity.virtual_entities())
                else:
                    skipped_entity_callback(entity, 'unsupported non-uniform scaling')
            except InsertTransformationError:
                # INSERT entity can not represented in the target coordinate system defined
                # by transformation matrix `m`.
                # Yield transformed sub-entities of the INSERT entity:
                yield from transform(virtual_block_reference_entities(entity, skipped_entity_callback))
            else:
                yield entity

    m = block_ref.matrix44()
    block_layout = block_ref.block()
    if block_layout is None:
        raise DXFStructureError(f'Required block definition for "{block_ref.dxf.name}" does not exist.')

    yield from transform(disassemble(block_layout))


def explode_entity(entity: 'DXFGraphic', target_layout: 'BaseLayout' = None) -> 'EntityQuery':
    """
    Explode parts of an entity as primitives into target layout, if target layout is ``None``,
    the target layout is the layout of the source entity.

    Returns an :class:`~ezdxf.query.EntityQuery` container with all DXF parts.

    Args:
        entity: DXF entity to explode, has to have a :meth:`virtual_entities()` method
        target_layout: target layout for DXF parts, ``None`` for same layout as source entity

    .. versionadded:: 0.12

    (internal API)

    """
    dxftype = entity.dxftype()

    if not hasattr(entity, 'virtual_entities'):
        raise DXFTypeError(f'Can not explode entity {dxftype}.')

    if entity.doc is None:
        raise DXFStructureError(f'{dxftype} has to be assigned to a DXF document.')

    entitydb = entity.doc.entitydb
    if entitydb is None:
        raise DXFStructureError(f'Exploding {dxftype} requires an entity database.')

    if target_layout is None:
        target_layout = entity.get_layout()
        if target_layout is None:
            raise DXFStructureError(f'{dxftype} without layout assigment, specify target layout.')

    entities = []

    for e in entity.virtual_entities():
        entitydb.add(e)
        target_layout.add_entity(e)
        entities.append(e)

    source_layout = entity.get_layout()
    if source_layout is not None:
        source_layout.delete_entity(entity)
    else:
        entitydb.delete_entity(entity)
    return EntityQuery(entities)


def virtual_lwpolyline_entities(lwpolyline: 'LWPolyline') -> Iterable[Union['Line', 'Arc']]:
    """
    Yields 'virtual' entities of LWPOLYLINE as LINE or ARC objects.

    This entities are located at the original positions, but are not stored in the entity database, have no handle
    and are not assigned to any layout.

    (internal API)

    """
    assert lwpolyline.dxftype() == 'LWPOLYLINE'

    points = lwpolyline.get_points('xyb')
    if len(points) < 2:
        return

    if lwpolyline.closed:
        points.append(points[0])

    yield from _virtual_polyline_entities(
        points=points,
        elevation=lwpolyline.dxf.elevation,
        extrusion=lwpolyline.dxf.get('extrusion', None),
        dxfattribs=lwpolyline.graphic_properties(),
        doc=lwpolyline.doc,
    )


def virtual_polyline_entities(polyline: 'Polyline') -> Iterable[Union['Line', 'Arc', 'Face3d']]:
    """
    Yields 'virtual' entities of POLYLINE as LINE, ARC or 3DFACE objects.

    This entities are located at the original positions, but are not stored in the entity database, have no handle
    and are not assigned to any layout.

    (internal API)

    """
    assert polyline.dxftype() == 'POLYLINE'
    if polyline.is_2d_polyline:
        return virtual_polyline2d_entities(polyline)
    elif polyline.is_3d_polyline:
        return virtual_polyline3d_entities(polyline)
    elif polyline.is_polygon_mesh:
        return virtual_polymesh_entities(polyline)
    elif polyline.is_poly_face_mesh:
        return virtual_polyface_entities(polyline)
    return []


def virtual_polyline2d_entities(polyline: 'Polyline') -> Iterable[Union['Line', 'Arc']]:
    """
    Yields 'virtual' entities of 2D POLYLINE as LINE or ARC objects.

    This entities are located at the original positions, but are not stored in the entity database, have no handle
    and are not assigned to any layout.

    (internal API)

    """
    assert polyline.dxftype() == 'POLYLINE'
    assert polyline.is_2d_polyline
    if len(polyline.vertices) < 2:
        return

    points = [(v.dxf.location.x, v.dxf.location.y, v.dxf.bulge) for v in polyline.vertices]
    if polyline.is_closed:
        points.append(points[0])

    yield from _virtual_polyline_entities(
        points=points,
        elevation=Vector(polyline.dxf.get('elevation', (0, 0, 0))).z,
        extrusion=polyline.dxf.get('extrusion', None),
        dxfattribs=polyline.graphic_properties(),
        doc=polyline.doc,
    )


def _virtual_polyline_entities(points, elevation: float, extrusion: Vector, dxfattribs: dict, doc) -> Iterable[
    Union['Line', 'Arc']]:
    ocs = OCS(extrusion) if extrusion else OCS()
    prev_point = None
    prev_bulge = None

    for x, y, bulge in points:
        point = Vector(x, y, elevation)
        if prev_point is None:
            prev_point = point
            prev_bulge = bulge
            continue

        attribs = dict(dxfattribs)
        if prev_bulge != 0:
            center, start_angle, end_angle, radius = bulge_to_arc(prev_point, point, prev_bulge)
            if radius > 0:
                attribs['center'] = Vector(center.x, center.y, elevation)
                attribs['radius'] = radius
                attribs['start_angle'] = math.degrees(start_angle)
                attribs['end_angle'] = math.degrees(end_angle)
                if extrusion:
                    attribs['extrusion'] = extrusion
                yield factory.new(dxftype='ARC', dxfattribs=attribs, doc=doc)
        else:
            attribs['start'] = ocs.to_wcs(prev_point)
            attribs['end'] = ocs.to_wcs(point)
            yield factory.new(dxftype='LINE', dxfattribs=attribs, doc=doc)
        prev_point = point
        prev_bulge = bulge


def virtual_polyline3d_entities(polyline: 'Polyline') -> Iterable['Line']:
    """
    Yields 'virtual' entities of 3D POLYLINE as LINE objects.

    This entities are located at the original positions, but are not stored in the entity database, have no handle
    and are not assigned to any layout.

    (internal API)

    """
    assert polyline.dxftype() == 'POLYLINE'
    assert polyline.is_3d_polyline
    if len(polyline.vertices) < 2:
        return
    doc = polyline.doc
    vertices = polyline.vertices
    dxfattribs = polyline.graphic_properties()
    start = -1 if polyline.is_closed else 0
    for index in range(start, len(vertices) - 1):
        dxfattribs['start'] = vertices[index].dxf.location
        dxfattribs['end'] = vertices[index + 1].dxf.location
        yield factory.new(dxftype='LINE', dxfattribs=dxfattribs, doc=doc)


def virtual_polymesh_entities(polyline: 'Polyline') -> Iterable['Face3d']:
    """
    Yields 'virtual' entities of POLYMESH as 3DFACE objects.

    This entities are located at the original positions, but are not stored in the entity database, have no handle
    and are not assigned to any layout.

    (internal API)

    """
    polymesh = cast('Polymesh', polyline)
    assert polymesh.dxftype() == 'POLYLINE'
    assert polymesh.is_polygon_mesh

    doc = polymesh.doc
    mesh = polymesh.get_mesh_vertex_cache()
    dxfattribs = polymesh.graphic_properties()
    m_count = polymesh.dxf.m_count
    n_count = polymesh.dxf.n_count
    m_range = m_count - int(not polymesh.is_m_closed)
    n_range = n_count - int(not polymesh.is_n_closed)

    for m in range(m_range):
        for n in range(n_range):
            next_m = (m + 1) % m_count
            next_n = (n + 1) % n_count

            dxfattribs['vtx0'] = mesh[m, n]
            dxfattribs['vtx1'] = mesh[next_m, n]
            dxfattribs['vtx2'] = mesh[next_m, next_n]
            dxfattribs['vtx3'] = mesh[m, next_n]
            yield factory.new(dxftype='3DFACE', dxfattribs=dxfattribs, doc=doc)


def virtual_polyface_entities(polyline: 'Polyline') -> Iterable['Face3d']:
    """
    Yields 'virtual' entities of POLYFACE as 3DFACE objects.

    This entities are located at the original positions, but are not stored in the entity database, have no handle
    and are not assigned to any layout.

    (internal API)

    """
    assert polyline.dxftype() == 'POLYLINE'
    assert polyline.is_poly_face_mesh

    doc = polyline.doc
    vertices = polyline.vertices
    base_attribs = polyline.graphic_properties()

    face_records = (v for v in vertices if v.is_face_record)
    for face in face_records:
        face3d_attribs = dict(base_attribs)
        face3d_attribs.update(face.graphic_properties())
        invisible = 0
        pos = 1

        indices = ((face.dxf.get(name), name) for name in VERTEXNAMES if face.dxf.hasattr(name))
        for index, name in indices:
            # vertex indices are 1-based, negative indices indicate invisible edges
            if index < 0:
                index = abs(index)
                invisible += pos
            # python list `vertices` is 0-based
            face3d_attribs[name] = vertices[index - 1].dxf.location
            # vertex index bit encoded: 1=0b0001, 2=0b0010, 3=0b0100, 4=0b1000
            pos <<= 1

        face3d_attribs['invisible'] = invisible
        yield factory.new(dxftype='3DFACE', dxfattribs=face3d_attribs, doc=doc)


def virtual_leader_entities(leader: 'Leader') -> Iterable['DXFGraphic']:
    # Source: https://atlight.github.io/formats/dxf-leader.html
    # GDAL: DXF LEADER implementation:
    # https://github.com/OSGeo/gdal/blob/master/gdal/ogr/ogrsf_frmts/dxf/ogrdxf_leader.cpp
    # LEADER DXF Reference:
    # http://help.autodesk.com/view/OARX/2018/ENU/?guid=GUID-396B2369-F89F-47D7-8223-8B7FB794F9F3
    from ezdxf.entities import DimStyleOverride
    assert leader.dxftype() == 'LEADER'

    vertices = Vector.list(leader.vertices)  # WCS
    if len(vertices) < 2:
        # This LEADER entities should be removed by the auditor if loaded or
        # ignored at exporting, if created by an ezdxf-user (log).
        raise ValueError('More than 1 vertex required.')

    # Set default styling attributes values:
    dimtad = 1
    dimgap = 0.625
    dimscale = 1.0
    dimclrd = leader.dxf.color
    dimltype = leader.dxf.linetype
    dimlwd = leader.dxf.lineweight
    override = None
    doc = leader.doc
    if doc:  # get styling attributes from associated DIMSTYLE and/or XDATA override
        override = DimStyleOverride(cast('Dimension', leader))
        dimtad = override.get('dimtad', dimtad)
        dimgap = override.get('dimgap', dimgap)
        dimscale = override.get('dimscale', dimscale)
        if dimscale == 0.0:  # special but unknown meaning
            dimscale = 1.0
        dimclrd = override.get('dimclrd', dimclrd)
        dimltype = override.get('dimltype', dimltype)
        dimlwd = override.get('dimlwd', dimlwd)

    dxf = leader.dxf
    text_width = dxf.text_width
    hook_line_vector = Vector(dxf.horizontal_direction)

    if dxf.has_hookline:
        if dxf.hookline_direction == 1:
            hook_line_vector = -hook_line_vector
        if dimtad != 0 and text_width > 0:
            vertices.append(vertices[-1] + hook_line_vector * (dimgap * dimscale + text_width))

    dxfattribs = leader.graphic_properties()
    dxfattribs['color'] = dimclrd
    dxfattribs['linetype'] = dimltype
    dxfattribs['lineweight'] = dimlwd

    if dxfattribs.get('color') == BYBLOCK:
        dxfattribs['color'] = dxf.block_color

    if dxf.path_type == 1:  # Spline
        return  # todo: implement spline LEADER
    else:
        attribs = dict(dxfattribs)
        prev = vertices[0]
        for vertex in vertices[1:]:
            attribs['start'] = prev
            attribs['end'] = vertex
            yield factory.new(dxftype='LINE', dxfattribs=attribs, doc=leader.doc)
            prev = vertex

    if dxf.has_arrowhead and override:
        arrow_name = override.get('dimldrblk', '')
        if arrow_name is None:
            return
        size = override.get('dimasz', 0.625) * dimscale
        rotation = (vertices[0] - vertices[1]).angle_deg
        if doc and arrow_name in doc.blocks:
            dxfattribs.update({
                'name': arrow_name,
                'insert': vertices[0],
                'rotation': rotation,
                'xscale': size,
                'yscale': size,
                'zscale': size,
            })
            # create a virtual block reference
            insert = factory.new('INSERT', dxfattribs=dxfattribs, doc=doc)
            yield from insert.virtual_entities()
        else:  # render standard arrows
            yield from virtual_arrow(
                arrow_name,
                vertices[0],
                size,
                rotation,
                dxfattribs,
            )


def virtual_arrow(name: str, insert: Vector = Vector(), size: float = 0.625, rotation: float = 0,
                  dxfattribs=None, doc=None):
    from ezdxf.graphicsfactory import VirtualLayout
    from ezdxf.render.arrows import ARROWS
    if name in ARROWS:
        layout = VirtualLayout(doc)
        dxfattribs = dxfattribs or {}
        ARROWS.render_arrow(
            layout, name,
            insert=insert,
            size=size,
            rotation=rotation,
            dxfattribs=dxfattribs,
        )
        yield from layout.entities
