# Copyright (c) 2019-2021 Manfred Moitzi
# License: MIT License
from __future__ import annotations
from typing import (
    TYPE_CHECKING,
    Iterable,
    Sequence,
    Union,
    Iterator,
    Optional,
)
import array
import copy
from itertools import chain
from contextlib import contextmanager

from ezdxf.audit import AuditError
from ezdxf.lldxf import validator
from ezdxf.lldxf.attributes import (
    DXFAttr,
    DXFAttributes,
    DefSubclass,
    RETURN_DEFAULT,
    group_code_mapping,
)
from ezdxf.lldxf.const import (
    SUBCLASS_MARKER,
    DXF2000,
    DXFValueError,
    DXFStructureError,
    DXFIndexError,
)
from ezdxf.lldxf.packedtags import VertexArray, TagArray, TagList
from ezdxf.math import Matrix44, UVec
from ezdxf.tools import take2
from .dxfentity import base_class, SubclassProcessor
from .dxfgfx import DXFGraphic, acdb_entity
from .factory import register_entity

if TYPE_CHECKING:
    from ezdxf.entities import DXFNamespace, DXFEntity
    from ezdxf.lldxf.tagwriter import AbstractTagWriter
    from ezdxf.lldxf.tags import Tags
    from ezdxf.audit import Auditor

__all__ = ["Mesh", "MeshData"]

acdb_mesh = DefSubclass(
    "AcDbSubDMesh",
    {
        "version": DXFAttr(71, default=2),
        "blend_crease": DXFAttr(
            72,
            default=0,
            validator=validator.is_integer_bool,
            fixer=RETURN_DEFAULT,
        ),
        # 0 is no smoothing
        "subdivision_levels": DXFAttr(
            91,
            default=0,
            validator=validator.is_greater_or_equal_zero,
            fixer=RETURN_DEFAULT,
        ),
        # 92: Vertex count of level 0
        # 10: Vertex position, multiple entries
        # 93: Size of face list of level 0
        # 90: Face list item, >=3 possible
        #     90: length of face list
        #     90: 1st vertex index
        #     90: 2nd vertex index ...
        # 94: Edge count of level 0
        #     90: Vertex index of 1st edge
        #     90: Vertex index of 2nd edge
        # 95: Edge crease count of level 0
        #     95 same as 94, or how is the 'edge create value' associated to edge index
        # 140: Edge crease value
        #
        # Overriding properties: how does this work?
        # 90: Count of sub-entity which property has been overridden
        # 91: Sub-entity marker
        # 92: Count of property was overridden
        # 90: Property type
        #     0 = Color
        #     1 = Material
        #     2 = Transparency
        #     3 = Material mapper
    },
)
acdb_mesh_group_codes = group_code_mapping(acdb_mesh)


class EdgeArray(TagArray):
    DTYPE = "L"

    def __len__(self) -> int:
        return len(self.values) // 2

    def __iter__(self) -> Iterator[tuple[int, int]]:
        for edge in take2(self.values):
            yield edge

    def set_data(self, edges: Iterable[tuple[int, int]]) -> None:
        self.values = array.array(self.DTYPE, chain.from_iterable(edges))

    def export_dxf(self, tagwriter: AbstractTagWriter):
        # count = count of edges not tags!
        tagwriter.write_tag2(94, len(self.values) // 2)
        for index in self.values:
            tagwriter.write_tag2(90, index)


class FaceList(TagList):
    def __len__(self) -> int:
        return len(self.values)

    def __iter__(self) -> Iterable[array.array]:
        return iter(self.values)

    def export_dxf(self, tagwriter: AbstractTagWriter):
        # count = count of tags not faces!
        tagwriter.write_tag2(93, self.tag_count())
        for face in self.values:
            tagwriter.write_tag2(90, len(face))
            for index in face:
                tagwriter.write_tag2(90, index)

    def tag_count(self) -> int:
        return len(self.values) + sum(len(f) for f in self.values)

    def set_data(self, faces: Iterable[Sequence[int]]) -> None:
        _faces = []
        for face in faces:
            _faces.append(face_to_array(face))
        self.values = _faces


def face_to_array(face: Sequence[int]) -> array.array:
    max_index = max(face)
    if max_index < 256:
        dtype = "B"
    elif max_index < 65536:
        dtype = "I"
    else:
        dtype = "L"
    return array.array(dtype, face)


def create_vertex_array(tags: Tags, start_index: int) -> VertexArray:
    vertex_tags = tags.collect_consecutive_tags(codes=(10,), start=start_index)
    return VertexArray(data=chain.from_iterable(t.value for t in vertex_tags))


def create_face_list(tags: Tags, start_index: int) -> FaceList:
    faces = FaceList()
    faces_list = faces.values
    face: list[int] = []
    counter = 0
    for tag in tags.collect_consecutive_tags(codes=(90,), start=start_index):
        if not counter:
            # leading counter tag
            counter = tag.value
            if face:
                # group code 90 = 32 bit integer
                faces_list.append(face_to_array(face))
                face = []
        else:
            # followed by count face tags
            counter -= 1
            face.append(tag.value)

    # add last face
    if face:
        # group code 90 = 32 bit integer
        faces_list.append(face_to_array(face))

    return faces


def create_edge_array(tags: Tags, start_index: int) -> EdgeArray:
    return EdgeArray(
        data=collect_values(tags, start_index, code=90)
    )  # int values


def collect_values(
    tags: Tags, start_index: int, code: int
) -> Iterable[Union[float, int]]:
    values = tags.collect_consecutive_tags(codes=(code,), start=start_index)
    return (t.value for t in values)


def create_crease_array(tags: Tags, start_index: int) -> array.array:
    return array.array(
        "f", collect_values(tags, start_index, code=140)
    )  # float values


COUNT_ERROR_MSG = "'MESH (#{}) without {} count.'"


@register_entity
class Mesh(DXFGraphic):
    """DXF MESH entity"""

    DXFTYPE = "MESH"
    DXFATTRIBS = DXFAttributes(base_class, acdb_entity, acdb_mesh)
    MIN_DXF_VERSION_FOR_EXPORT = DXF2000

    def __init__(self):
        super().__init__()
        self._vertices = VertexArray()  # vertices stored as array.array('d')
        self._faces = FaceList()  # face lists data
        self._edges = EdgeArray()  # edge indices stored as array.array('L')
        self._creases = array.array("f")  # creases stored as array.array('f')

    def _copy_data(self, entity: DXFEntity) -> None:
        """Copy data: vertices, faces, edges, creases."""
        assert isinstance(entity, Mesh)
        entity._vertices = copy.deepcopy(self._vertices)
        entity._faces = copy.deepcopy(self._faces)
        entity._edges = copy.deepcopy(self._edges)
        entity._creases = copy.deepcopy(self._creases)

    def load_dxf_attribs(
        self, processor: Optional[SubclassProcessor] = None
    ) -> DXFNamespace:
        dxf = super().load_dxf_attribs(processor)
        if processor:
            tags = processor.subclass_by_index(2)
            if tags:
                # Load mesh data and remove their tags from subclass
                self.load_mesh_data(tags, dxf.handle)
                # Load remaining data into name space
                processor.fast_load_dxfattribs(
                    dxf, acdb_mesh_group_codes, 2, recover=True
                )
            else:
                raise DXFStructureError(
                    f"missing 'AcDbSubMesh' subclass in MESH(#{dxf.handle})"
                )
        return dxf

    def load_mesh_data(self, mesh_tags: Tags, handle: str) -> None:
        def process_vertices():
            try:
                vertex_count_index = mesh_tags.tag_index(92)
            except DXFValueError:
                raise DXFStructureError(
                    COUNT_ERROR_MSG.format(handle, "vertex")
                )
            vertices = create_vertex_array(mesh_tags, vertex_count_index + 1)
            # Remove vertex count tag and all vertex tags
            end_index = vertex_count_index + 1 + len(vertices)
            del mesh_tags[vertex_count_index:end_index]
            return vertices

        def process_faces():
            try:
                face_count_index = mesh_tags.tag_index(93)
            except DXFValueError:
                raise DXFStructureError(COUNT_ERROR_MSG.format(handle, "face"))
            else:
                # Remove face count tag and all face tags
                faces = create_face_list(mesh_tags, face_count_index + 1)
                end_index = face_count_index + 1 + faces.tag_count()
                del mesh_tags[face_count_index:end_index]
                return faces

        def process_edges():
            try:
                edge_count_index = mesh_tags.tag_index(94)
            except DXFValueError:
                raise DXFStructureError(COUNT_ERROR_MSG.format(handle, "edge"))
            else:
                edges = create_edge_array(mesh_tags, edge_count_index + 1)
                # Remove edge count tag and all edge tags
                end_index = edge_count_index + 1 + len(edges.values)
                del mesh_tags[edge_count_index:end_index]
                return edges

        def process_creases():
            try:
                crease_count_index = mesh_tags.tag_index(95)
            except DXFValueError:
                raise DXFStructureError(
                    COUNT_ERROR_MSG.format(handle, "crease")
                )
            else:
                creases = create_crease_array(mesh_tags, crease_count_index + 1)
                # Remove crease count tag and all crease tags
                end_index = crease_count_index + 1 + len(creases)
                del mesh_tags[crease_count_index:end_index]
                return creases

        self._vertices = process_vertices()
        self._faces = process_faces()
        self._edges = process_edges()
        self._creases = process_creases()

    def export_entity(self, tagwriter: AbstractTagWriter) -> None:
        """Export entity specific data as DXF tags."""
        super().export_entity(tagwriter)
        tagwriter.write_tag2(SUBCLASS_MARKER, acdb_mesh.name)
        self.dxf.export_dxf_attribs(
            tagwriter, ["version", "blend_crease", "subdivision_levels"]
        )
        self.export_mesh_data(tagwriter)
        self.export_override_data(tagwriter)

    def export_mesh_data(self, tagwriter: AbstractTagWriter):
        tagwriter.write_tag2(92, len(self.vertices))
        self._vertices.export_dxf(tagwriter, code=10)
        self._faces.export_dxf(tagwriter)
        self._edges.export_dxf(tagwriter)

        creases = self._fixed_crease_values()
        tagwriter.write_tag2(95, len(self.creases))
        for crease_value in creases:
            tagwriter.write_tag2(140, crease_value)

    def _fixed_crease_values(self) -> list[float]:
        # The edge count has to match the crease count, otherwise its an invalid
        # DXF file to AutoCAD!
        edge_count = len(self._edges)
        creases = list(self.creases)
        crease_count = len(creases)
        if edge_count < crease_count:
            creases = creases[:edge_count]
        while edge_count > len(creases):
            creases.append(0.0)
        return creases

    def export_override_data(self, tagwriter: AbstractTagWriter):
        tagwriter.write_tag2(90, 0)

    @property
    def creases(self) -> array.array:
        """Creases as :class:`array.array`. (read/write)"""
        return self._creases

    @creases.setter
    def creases(self, values: Iterable[float]) -> None:
        self._creases = array.array("f", values)

    @property
    def vertices(self):
        """Vertices as list like :class:`~ezdxf.lldxf.packedtags.VertexArray`.
        (read/write)
        """
        return self._vertices

    @vertices.setter
    def vertices(self, points: Iterable[UVec]) -> None:
        self._vertices = VertexArray(chain.from_iterable(points))

    @property
    def edges(self):
        """Edges as list like :class:`~ezdxf.lldxf.packedtags.TagArray`.
        (read/write)
        """
        return self._edges

    @edges.setter
    def edges(self, edges: Iterable[tuple[int, int]]) -> None:
        self._edges.set_data(edges)

    @property
    def faces(self):
        """Faces as list like :class:`~ezdxf.lldxf.packedtags.TagList`.
        (read/write)
        """
        return self._faces

    @faces.setter
    def faces(self, faces: Iterable[Sequence[int]]) -> None:
        self._faces.set_data(faces)

    def get_data(self) -> MeshData:
        return MeshData(self)

    def set_data(self, data: MeshData) -> None:
        self.vertices = data.vertices
        self._faces.set_data(data.faces)
        self._edges.set_data(data.edges)
        self.creases = array.array("f", data.edge_crease_values)
        if len(self.edges) != len(self.creases):
            raise DXFValueError("count of edges must match count of creases")

    @contextmanager
    def edit_data(self) -> Iterator[MeshData]:
        """Context manager various mesh data, returns :class:`MeshData`.

        Despite that vertices, edge and faces since `ezdxf` v0.8.9 are
        accessible as packed data types, the usage of :class:`MeshData`
        by context manager :meth:`edit_data` is still recommended.

        """
        data = self.get_data()
        yield data
        self.set_data(data)

    def transform(self, m: Matrix44) -> Mesh:
        """Transform the MESH entity by transformation matrix `m` inplace."""
        self._vertices.transform(m)
        self.post_transform(m)
        return self

    def audit(self, auditor: Auditor) -> None:
        if not self.is_alive:
            return
        super().audit(auditor)
        if len(self.edges) != len(self.creases):
            self.creases = self._fixed_crease_values()  # type: ignore
            auditor.fixed_error(
                code=AuditError.INVALID_CREASE_VALUE_COUNT,
                message=f"fixed invalid count of crease values in {str(self)}",
                dxf_entity=self,
            )


class MeshData:
    def __init__(self, mesh) -> None:
        self.vertices: list[Sequence[float]] = list(mesh.vertices)
        self.faces: list[array.array] = list(mesh.faces)
        self.edges: list[tuple[int, int]] = list(mesh.edges)
        self.edge_crease_values: list[float] = list(mesh.creases)

    def add_face(self, vertices: Iterable[UVec]) -> Sequence[int]:
        """Add a face by coordinates, vertices is a list of ``(x, y, z)``
        tuples.
        """
        return self.add_entity(vertices, self.faces)

    def add_edge_crease(self, v1: int, v2: int, crease: float):
        """Add an edge crease value, the edge is defined by the vertex indices
        `v1` and `v2`.
        The crease value defines the amount of subdivision that will be applied
        to this edge.
        A crease value of the subdivision level prevents the edge from
        deformation and a value of 0.0 means no protection from
        subdividing.
        """
        if v1 < 0 or v1 > len(self.vertices):
            raise DXFIndexError("vertex index `v1` out of range")
        if v2 < 0 or v2 > len(self.vertices):
            raise DXFIndexError("vertex index `v2` out of range")
        self.edges.append((v1, v2))
        self.edge_crease_values.append(crease)

    def add_entity(
        self, vertices: Iterable[UVec], entity_list: list
    ) -> Sequence[int]:
        indices = [self.add_vertex(vertex) for vertex in vertices]
        entity_list.append(indices)
        return indices

    def add_vertex(self, vertex: UVec) -> int:
        if len(vertex) != 3:
            raise DXFValueError(
                "Parameter vertex has to be a 3-tuple (x, y, z)."
            )
        index = len(self.vertices)
        self.vertices.append(tuple(vertex))
        return index

    def optimize(self, precision: int = 6):
        """
        Try to reduce vertex count by merging near vertices. `precision`
        defines the decimal places for coordinate be equal to merge two vertices.

        """

        def remove_doublette_vertices() -> dict[int, int]:
            def prepare_vertices() -> Iterable[tuple[float, float, float, int]]:
                for index, vertex in enumerate(self.vertices):
                    x, y, z = vertex
                    yield (
                        round(x, precision),
                        round(y, precision),
                        round(z, precision),
                        index,
                    )

            sorted_vertex_list = list(sorted(prepare_vertices()))
            original_vertices = self.vertices
            self.vertices = []
            index_map: dict[int, int] = {}
            cmp_vertex = None
            index = 0
            while len(sorted_vertex_list):
                vertex_entry = sorted_vertex_list.pop()
                original_index = vertex_entry[3]
                vertex = original_vertices[original_index]
                if vertex != cmp_vertex:
                    # this is not a doublette
                    index = len(self.vertices)
                    self.vertices.append(vertex)
                    index_map[original_index] = index
                    cmp_vertex = vertex
                else:  # it is a doublette
                    index_map[original_index] = index
            return index_map

        def remap_faces() -> None:
            self.faces = remap_indices(self.faces)  # type: ignore

        def remap_edges() -> None:
            self.edges = remap_indices(self.edges)  # type: ignore

        def remap_indices(entity_list: Sequence[Sequence[int]]) -> list[tuple]:
            mapped_indices: list[tuple] = []
            for entity in entity_list:
                index_list = [index_map[index] for index in entity]
                mapped_indices.append(tuple(index_list))
            return mapped_indices

        index_map = remove_doublette_vertices()
        remap_faces()
        remap_edges()
