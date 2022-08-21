#  Copyright (c) 2022, Manfred Moitzi
#  License: MIT License

"""
This module provides support for fonts stored as SHX and SHP files.
(SHP is not the GIS file format!)

The documentation about the SHP file format can be found at Autodesk:
https://help.autodesk.com/view/OARX/2018/ENU/?guid=GUID-DE941DB5-7044-433C-AA68-2A9AE98A5713

"""
import math
from typing import (
    Dict,
    Sequence,
    Iterable,
    Iterator,
    Callable,
    List,
    Tuple,
    Optional,
    Any,
)
import enum
import dataclasses
import pathlib

from ezdxf import path
from ezdxf.math import UVec, Vec2, Vec3, ConstructionEllipse, bulge_to_arc


class ShapeFileException(Exception):
    pass


class UnsupportedShapeFile(ShapeFileException):
    pass


class InvalidFontDefinition(ShapeFileException):
    pass


class InvalidFontParameters(ShapeFileException):
    pass


class InvalidShapeRecord(ShapeFileException):
    pass


class FileStructureError(ShapeFileException):
    pass


class StackUnderflow(ShapeFileException):
    pass


class FontEmbedding(enum.IntEnum):
    ALLOWED = 0
    DISALLOWED = 1
    READONLY = 2


class FontEncoding(enum.IntEnum):
    UNICODE = 0
    PACKED_MULTIBYTE_1 = 1
    SHAPE_FILE = 2


class FontMode(enum.IntEnum):
    HORIZONTAL = 0
    BIDIRECT = 2


NO_DATA: Sequence[int] = tuple()
DEBUG = False
DEBUG_CODES = set()
DEBUG_SHAPE_NUMBERS = set()


# slots=True - Python 3.10+
@dataclasses.dataclass
class Symbol:
    number: int
    byte_count: int
    name: str
    data: Sequence[int] = NO_DATA

    def export_str(self, as_num: int = 0) -> List[str]:
        num = as_num if as_num else self.number
        export = [f"*{num:05X},{self.byte_count},{self.name}"]
        export.extend(format_shape_data_string(self.data))
        return export


def format_shape_data_string(data: Sequence[int]) -> List[str]:
    export = []
    s = ""
    for num in data:
        s2 = s + f"{num},"
        if len(s2) < 80:
            s = s2
        else:
            export.append(s[:-1])
            s = f",{num},"
    if s:
        export.append(s[:-1])
    return export


class ShapeFile:
    def __init__(
        self,
        name: str,
        above: int,
        below: int,
        mode=FontMode.HORIZONTAL,
        encoding=FontEncoding.UNICODE,
        embed=FontEmbedding.ALLOWED,
    ):
        self.shapes: Dict[int, Symbol] = dict()
        self.name = name
        self.above = above
        self.below = below
        self.mode = mode
        self.encoding = encoding
        self.embed = embed

    @property
    def cap_height(self) -> float:
        return float(self.above)

    @property
    def descender(self) -> float:
        return float(self.below)

    @property
    def is_font(self) -> bool:
        return self.encoding != FontEncoding.SHAPE_FILE

    @property
    def is_shape_file(self) -> bool:
        return self.encoding == FontEncoding.SHAPE_FILE

    def find(self, name: str) -> Optional[Symbol]:
        for symbol in self.shapes.values():
            if name == symbol.name:
                return symbol
        return None

    def __len__(self):
        return len(self.shapes)

    def __getitem__(self, item):
        return self.shapes[item]

    @staticmethod
    def from_str_record(record: Sequence[str]):
        if len(record) == 2:
            encoding = FontEncoding.UNICODE
            above = 0
            below = 0
            embed = FontEmbedding.ALLOWED
            mode = FontMode.HORIZONTAL
            end = 0
            header, params = record
            try:
                # ignore second value: defbytes
                spec, _, name = header.split(",", maxsplit=2)
            except ValueError:
                raise InvalidFontDefinition()
            if spec == "*UNIFONT":
                try:
                    above, below, mode, encoding, embed, end = params.split(",")  # type: ignore
                except ValueError:
                    raise InvalidFontParameters(params)
            elif spec == "*0":
                try:
                    above, below, mode, *rest = params.split(",")  # type: ignore
                    end = rest[-1]  # type: ignore
                except ValueError:
                    raise InvalidFontParameters(params)
            else:  # it's a simple shape file
                encoding = FontEncoding.SHAPE_FILE
            assert int(end) == 0

            return ShapeFile(
                name.strip(),
                int(above),
                int(below),
                FontMode(int(mode)),
                FontEncoding(int(encoding)),
                FontEmbedding(int(embed)),
            )

    def parse_str_records(self, records: Iterable[Sequence[str]]) -> None:
        for record in records:
            if len(record) < 2:
                raise InvalidShapeRecord(str(record))
            try:
                number, byte_count, name = split_def_record(record[0])
            except ValueError:
                raise FileStructureError(record[0])

            assert len(number) > 1
            if number[1] == "0":  # hex if first char is "0" like "*0000A"
                int_num = int(number[1:], 16)
            else:  # like "*130"
                int_num = int(number[1:], 10)

            symbol = Symbol(int_num, int(byte_count), name)
            data = "".join(record[1:])
            symbol.data = tuple(parse_codes(split_record(data)))
            if symbol.data[-1] == 0:
                self.shapes[int_num] = symbol
            else:
                raise FileStructureError(
                    f"file structure error at symbol <{record[0]}>"
                )

    def get_codes(self, number: int) -> Sequence[int]:
        symbol = self.shapes.get(number)
        if symbol is None:
            return tuple()  # return codes for non-printable chars
        return symbol.data

    def render_shape(self, number: int, stacked=False) -> path.Path:
        return render_shapes([number], self.get_codes, stacked=stacked)

    def render_shapes(self, numbers: Sequence[int], stacked=False) -> path.Path:
        return render_shapes(numbers, self.get_codes, stacked=stacked)

    def render_text(self, text: str, stacked=False) -> path.Path:
        numbers = [ord(char) for char in text]
        return render_shapes(
            numbers, self.get_codes, stacked=stacked, reset_to_baseline=True
        )

    def shape_string(self, shape_number: int, as_num: int = 0) -> List[str]:
        return self.shapes[shape_number].export_str(as_num)


def readfile(filename: str) -> ShapeFile:
    """Load shape-file `filename`, the file type is detected by the file
    name extension and should be ".shp" or ".shx" otherwise rises an
    exception :class:`UnsupportedShapeFile`.
    """
    if filename.lower().endswith(".shp"):
        # ignore non-ascii characters in comments and names
        shp_data = pathlib.Path(filename).read_text(errors="ignore")
        return shp_loads(shp_data)
    elif filename.lower().endswith(".shx"):
        shx_data = pathlib.Path(filename).read_bytes()
        return shx_loadb(shx_data)
    else:
        raise UnsupportedShapeFile("unknown filetype")


def split_def_record(record: str) -> Sequence[str]:
    return tuple(s.strip() for s in record.split(",", maxsplit=2))


def split_record(record: str) -> Sequence[str]:
    return tuple(s.strip() for s in record.split(","))


def parse_codes(codes: Iterable[str]) -> Iterator[int]:
    for code in codes:
        code = code.strip("()")
        if code == "":
            continue
        if code[0] == "0":
            yield int(code, 16)
        elif code[0] == "-" and code[1] == "0":
            yield int(code, 16)
        else:
            yield int(code, 10)


def shp_loads(data: str) -> ShapeFile:
    records = parse_string_records(merge_lines(filter_noise(data.split("\n"))))
    if "*UNIFONT" in records:
        font_definition = records.pop("*UNIFONT")
    elif "*0" in records:
        font_definition = records.pop("*0")
    else:
        # a common shape file without a name
        # symbol numbers are decimal!!!
        font_definition = ("_,_,_", "")
    shp = ShapeFile.from_str_record(font_definition)
    shp.parse_str_records(records.values())
    return shp


def shx_loadb(data: bytes) -> ShapeFile:
    if data.startswith(b"AutoCAD-86 shapes 1.0"):
        return load_shx_shape_file_1_0(data)
    elif data.startswith(b"AutoCAD-86 shapes 1.1"):
        return load_shx_shape_file_1_1(data)
    elif data.startswith(b"AutoCAD-86 unifont 1.0"):
        return load_shx_unifont_file_1_0(data)
    elif data.startswith(b"AutoCAD-86 bigfont 1.0"):
        raise UnsupportedShapeFile("BIGFONT shapes are not supported yet")
    raise UnsupportedShapeFile("unknown shape file format")


def load_shx_shape_file_1_0(data: bytes) -> ShapeFile:
    above = 0
    below = 0
    mode = FontMode.HORIZONTAL
    name = ""
    shapes = parse_shx_shapes(data)
    font_definition = shapes.get(0)
    if font_definition:
        shapes.pop(0)
        shape_data = font_definition.data
        above = shape_data[0]
        below = shape_data[1]
        mode = FontMode(shape_data[2])

    shape_file = ShapeFile(name, above=above, below=below, mode=mode)
    shape_file.shapes = shapes
    return shape_file


def load_shx_shape_file_1_1(data: bytes) -> ShapeFile:
    # seems to be the same as v1.0:
    return load_shx_shape_file_1_0(data)


class DataReader:
    def __init__(self, data: bytes, index=0):
        self.data = data
        self.index = index

    @property
    def has_data(self) -> bool:
        return self.index < len(self.data)

    def skip(self, n: int) -> None:
        self.index += n

    def u8(self) -> int:
        index = self.index
        self.index += 1
        return self.data[index]

    def i8(self) -> int:
        value = self.data[self.index]
        self.index += 1
        if value > 127:
            return (value & 127) - 128  # ???
        return value

    def octant(self) -> int:
        value = self.data[self.index]
        self.index += 1
        if value & 128:
            return -(value & 127)
        else:
            return value

    def read_str(self) -> str:
        s = ""
        while True:
            char = self.u8()
            if char == 0:
                return s
            else:
                s += chr(char)

    def read_bytes(self, n: int) -> bytes:
        index = self.index
        self.index += n
        return self.data[index : index + n]

    def u16(self) -> int:
        # little ending
        index = self.index
        self.index += 2
        return self.data[index] + (self.data[index + 1] << 8)


# the old 'AutoCAD-86 shapes 1.0' format
SHX_SHAPES_START_INDEX = 0x17


def parse_shx_shapes(data: bytes) -> Dict[int, Symbol]:
    shapes: Dict[int, Symbol] = dict()
    reader = DataReader(data, SHX_SHAPES_START_INDEX)
    if reader.u8() != 0x1A:
        raise FileStructureError("signature byte 0x1A not found")
    first_number = reader.u16()
    last_number = reader.u16()
    shape_count = reader.u16()
    index_table: List[Tuple[int, int]] = []
    for _ in range(shape_count):
        shape_number = reader.u16()
        data_size = reader.u16()
        index_table.append((shape_number, data_size))
    if index_table[0][0] != first_number:
        raise FileStructureError("invalid first entry in index table")
    if index_table[-1][0] != last_number:
        raise FileStructureError("invalid last entry in index table")
    for shape_number, length in index_table:
        record = reader.read_bytes(length)
        try:
            name, shape_data = parse_shx_data_record(record)
        except IndexError:
            raise FileStructureError(
                f"SHX parsing error shape *{shape_number:05X}: {str(record)}"
            )
        byte_count = length - len(name) - 1
        shapes[shape_number] = Symbol(
            shape_number, byte_count, name, shape_data
        )
    if reader.read_bytes(3) != b"EOF":
        raise FileStructureError("EOF marker not found")
    return shapes


def parse_shx_data_record(data: bytes) -> Tuple[str, Sequence[int]]:
    reader = DataReader(data)
    name = reader.read_str()
    codes = parse_shape_codes(reader)
    return name, codes


# the new 'AutoCAD-86 unifont 1.0' format
SHX_UNIFONT_START_INDEX = 0x18


def load_shx_unifont_file_1_0(data: bytes) -> ShapeFile:
    reader = DataReader(data, SHX_UNIFONT_START_INDEX)
    name, above, below, mode, encoding, embed = parse_shx_unifont_definition(
        reader
    )
    shape_file = ShapeFile(
        name,
        above=above,
        below=below,
        mode=mode,
        encoding=encoding,
        embed=embed,
    )
    try:
        shape_file.shapes = parse_shx_unifont_shapes(reader)
    except IndexError:
        raise FileStructureError("pre-mature end of file")
    return shape_file


def parse_shx_unifont_definition(reader: DataReader) -> Sequence[Any]:

    if reader.u8() != 0x1A:
        raise FileStructureError("signature byte 0x1A not found")
    reader.skip(6)
    # u16 record count: in isocp.shx = 238 (0xEE 0x00)
    #     = 1 definition record + 237 character records
    # u16 isocp.shx: 0 (0x00 0x00), always 0???
    # u16 font definition record size; isocp.shx: 52 (0x34 0x00)

    name = reader.read_str()
    above = reader.u8()
    below = reader.u8()
    mode = FontMode(reader.u8())
    encoding = FontEncoding(reader.u8())
    embed = FontEmbedding(reader.u8())
    reader.skip(1)  # <00> byte - end of font definition
    return name, above, below, mode, encoding, embed


def parse_shx_unifont_shapes(reader: DataReader) -> Dict[int, Symbol]:
    shapes: Dict[int, Symbol] = dict()
    while reader.has_data:
        shape_number = reader.u16()
        byte_count = reader.u16()
        name = reader.read_str()
        byte_count = byte_count - len(name) - 1
        data_record = reader.read_bytes(byte_count)
        codes = parse_shape_codes(DataReader(data_record), unifont=True)
        shapes[shape_number] = Symbol(shape_number, byte_count, name, codes)
    return shapes


SINGLE_CODES = {1, 2, 5, 6, 14}


def parse_shape_codes(reader: DataReader, unifont=False) -> Sequence[int]:
    codes: List[int] = []
    while True:
        code = reader.u8()
        codes.append(code)
        if code == 0:
            return tuple(codes)
        elif code in SINGLE_CODES or code > 14:
            continue
        elif code == 3 or code == 4:  # size control
            codes.append(reader.u8())
        elif code == 7:  # sub shape
            if unifont:
                codes.append(reader.u16())
            else:
                codes.append(reader.u8())
        elif code == 8:  # displacement
            codes.append(reader.i8())
            codes.append(reader.i8())
        elif code == 9:  # multiple displacement
            x, y = 1, 1
            while x or y:
                x = reader.i8()
                y = reader.i8()
                codes.append(x)
                codes.append(y)
        elif code == 10:  # octant arc
            codes.append(reader.u8())  # radius
            codes.append(reader.octant())  # octant specs
        elif code == 11:  # fractional arc
            codes.append(reader.u8())  # start offset
            codes.append(reader.u8())  # end offset
            codes.append(reader.u8())  # radius hi
            codes.append(reader.u8())  # radius lo
            codes.append(reader.octant())  # octant specs
        elif code == 12:  # bulge arcs
            codes.append(reader.i8())  # x
            codes.append(reader.i8())  # y
            codes.append(reader.i8())  # bulge
        elif code == 13:  # multiple bulge arcs
            x, y = 1, 1
            while x or y:
                x = reader.i8()
                y = reader.i8()
                codes.append(x)
                codes.append(y)
                if x or y:
                    codes.append(reader.i8())


def filter_noise(lines: Iterable[str]) -> Iterator[str]:
    for line in lines:
        line = line.strip()
        if line:
            line = line.split(";")[0]
            line = line.strip()
            if line:
                yield line


def merge_lines(lines: Iterable[str]) -> Iterator[str]:
    current = ""
    for next_line in lines:
        if not current:
            current = next_line
        elif current.startswith("*"):
            if next_line.startswith(","):  # wrapped specification line?
                current += next_line
            else:
                yield current
                current = next_line
        elif current.endswith(","):
            current += next_line
        elif next_line.startswith(","):
            current += next_line
        else:
            yield current
            current = next_line
    if current:
        yield current


def parse_string_records(lines: Iterable[str]) -> Dict[str, Sequence[str]]:
    records: Dict[str, Sequence[str]] = dict()
    name = None
    record = []
    for line in lines:
        if line.startswith("*BIGFONT"):
            raise UnsupportedShapeFile(
                "BIGFONT shape files are not supported yet"
            )
        if line.startswith("*"):
            if name is not None:
                records[name] = tuple(record)
            name = line.split(",")[0].strip()
            record = [line]
        else:
            record.append(line)

    if name is not None:
        records[name] = tuple(record)
    return records


def render_shapes(
    shape_numbers: Sequence[int],
    get_codes: Callable[[int], Sequence[int]],
    stacked: bool,
    start: UVec = (0, 0),
    reset_to_baseline=False,
) -> path.Path:
    ctx = ShapeRenderer(
        path.Path(start),
        pen_down=True,
        stacked=stacked,
        get_codes=get_codes,
    )
    for shape_number in shape_numbers:
        try:
            ctx.render(shape_number, reset_to_baseline=reset_to_baseline)
        except StackUnderflow:
            raise StackUnderflow(
                f"stack underflow while rendering shape number {shape_number}"
            )
        # move cursor to the start of the next char???
    return ctx.p


#        0, 1, 2,   3, 4,    5,  6,  7,  8,  9,  A,    B, C,   D, E, F
VEC_X = [1, 1, 1, 0.5, 0, -0.5, -1, -1, -1, -1, -1, -0.5, 0, 0.5, 1, 1]
#        0,   1, 2, 3, 4, 5, 6,   7, 8,    9,  A,  B,  C,  D,  E,    F
VEC_Y = [0, 0.5, 1, 1, 1, 1, 1, 0.5, 0, -0.5, -1, -1, -1, -1, -1, -0.5]


class ShapeRenderer:
    def __init__(
        self,
        p: path.Path,
        get_codes: Callable[[int], Sequence[int]],
        *,
        vector_length: float = 1.0,
        pen_down: bool = True,
        stacked: bool = False,
    ):
        self.p = p
        self.vector_length = float(vector_length)  # initial vector length
        self.pen_down = pen_down
        self.stacked = stacked  # vertical stacked text
        self._location_stack: List[Vec3] = []
        self._get_codes = get_codes
        self._baseline_y = self.p.start.y

    @property
    def current_location(self) -> Vec3:
        return self.p.end

    def push(self) -> None:
        self._location_stack.append(self.current_location)

    def pop(self) -> None:
        self.p.move_to(self._location_stack.pop())

    def render(
        self,
        shape_number: int,
        reset_to_baseline=False,
    ) -> None:
        self.pen_down = True
        codes = self._get_codes(shape_number)
        index = 0
        skip_next = False
        while index < len(codes):
            code = codes[index]
            if DEBUG and (code in DEBUG_CODES) and shape_number:
                DEBUG_SHAPE_NUMBERS.add(shape_number)
            index += 1
            if code > 15 and not skip_next:
                self.draw_vector(code)
            elif code == 0:
                break
            elif code == 1 and not skip_next:  # pen down
                self.pen_down = True
            elif code == 2 and not skip_next:  # pen up
                self.pen_down = False
            elif code == 3 or code == 4:  # scale size
                factor = codes[index]
                index += 1
                if not skip_next:
                    if code == 3:
                        self.vector_length /= factor
                    elif code == 4:
                        self.vector_length *= factor
                    continue
            elif code == 5 and not skip_next:  # push location state
                self.push()
            elif code == 6 and not skip_next:  # pop location state
                try:
                    self.pop()
                except IndexError:
                    raise StackUnderflow()
            elif code == 7:  # sub-shape:
                sub_shape_number = codes[index]
                index += 1
                if not skip_next:
                    # Use current state of pen and location!
                    self.render(sub_shape_number)
                    # resume with current state of pen and location!
            elif code == 8:  # displacement vector
                x = codes[index]
                y = codes[index + 1]
                index += 2
                if not skip_next:
                    self.draw_displacement(x, y)
            elif code == 9:  # multiple displacements vectors
                while True:
                    x = codes[index]
                    y = codes[index + 1]
                    index += 2
                    if x == 0 and y == 0:
                        break
                    if not skip_next:
                        self.draw_displacement(x, y)
            elif code == 10:  # 10 octant arc
                radius = codes[index]
                start_octant, octant_span, ccw = decode_octant_specs(
                    codes[index + 1]
                )
                if octant_span == 0:  # full circle
                    octant_span = 8
                index += 2
                if not skip_next:
                    self.draw_arc_span(
                        radius * self.vector_length,
                        math.radians(start_octant * 45),
                        math.radians(octant_span * 45),
                        ccw,
                    )
            elif code == 11:  # fractional arc
                # TODO: this still seems not 100% correct, see vertical placing
                #  of characters "9" and "&" for font isocp.shx.
                #  This is solved by placing the end point on the baseline after
                #  each character rendering, but only for text rendering.
                #  The remaining problems can possibly be due to a loss of
                #  precision when converting floats to ints.
                start_offset = codes[index]
                end_offset = codes[index + 1]
                radius = (codes[index + 2] << 8) + codes[index + 3]
                start_octant, octant_span, ccw = decode_octant_specs(
                    codes[index + 4]
                )
                index += 5
                if end_offset == 0:
                    end_offset = 256
                binary_deg = 45.0 / 256.0
                start_offset_angle = start_offset * binary_deg
                end_offset_angle = end_offset * binary_deg
                if ccw:
                    end_octant = start_octant + octant_span - 1
                    start_angle = start_octant * 45.0 + start_offset_angle
                    end_angle = end_octant * 45.0 + end_offset_angle
                else:
                    end_octant = start_octant - octant_span + 1
                    start_angle = start_octant * 45.0 - start_offset_angle
                    end_angle = end_octant * 45.0 - end_offset_angle

                if not skip_next:
                    self.draw_arc_start_to_end(
                        radius * self.vector_length,
                        math.radians(start_angle),
                        math.radians(end_angle),
                        ccw,
                    )
            elif code == 12:  # bulge arc
                x = codes[index]
                y = codes[index + 1]
                bulge = codes[index + 2]
                index += 3
                if not skip_next:
                    self.draw_bulge(x, y, bulge)
            elif code == 13:  # multiple bulge arcs
                while True:
                    x = codes[index]
                    y = codes[index + 1]
                    if x == 0 and y == 0:
                        index += 2
                        break
                    bulge = codes[index + 2]
                    index += 3
                    if not skip_next:
                        self.draw_bulge(x, y, bulge)
            elif code == 14:  # flag vertical text
                if not self.stacked:
                    skip_next = True
                    continue
            skip_next = False

        if reset_to_baseline:
            # HACK: because of invalid fractional arc rendering!
            if not math.isclose(self.p.end.y, self._baseline_y):
                self.p.move_to((self.p.end.x, self._baseline_y))

    def draw_vector(self, code: int) -> None:
        angle: int = code & 0xF
        length: int = (code >> 4) & 0xF
        self.draw_displacement(VEC_X[angle] * length, VEC_Y[angle] * length)

    def draw_displacement(self, x: float, y: float):
        scale = self.vector_length
        target = self.current_location + (x * scale, y * scale)
        if self.pen_down:
            self.p.line_to(target)
        else:
            self.p.move_to(target)

    def draw_arc_span(
        self, radius: float, start_angle: float, span_angle: float, ccw: bool
    ):
        # IMPORTANT: radius has to be scaled by self.vector_length!
        end_angle = start_angle + (span_angle if ccw else -span_angle)
        self.draw_arc_start_to_end(radius, start_angle, end_angle, ccw)

    def draw_arc_start_to_end(
        self, radius: float, start_angle: float, end_angle: float, ccw: bool
    ):
        # IMPORTANT: radius has to be scaled by self.vector_length!
        assert radius > 0.0
        arc = ConstructionEllipse(
            major_axis=(radius, 0),
            start_param=start_angle,
            end_param=end_angle,
            ccw=ccw,
        )
        # arc goes start -> end if ccw otherwise end -> start
        # move arc start-point to the end-point of current path
        arc.center += self.current_location - (
            arc.start_point if ccw else arc.end_point
        )
        if self.pen_down:
            path.add_ellipse(self.p, arc, reset=False)
        else:
            self.p.move_to(arc.end_point if ccw else arc.start_point)

    def draw_arc(
        self,
        center: Vec2,
        radius: float,
        start_param: float,
        end_param: float,
        ccw: bool,
    ):
        # IMPORTANT: radius has to be scaled by self.vector_length!
        arc = ConstructionEllipse(
            center=center,
            major_axis=(radius, 0),
            start_param=start_param,
            end_param=end_param,
            ccw=ccw,
        )
        if self.pen_down:
            path.add_ellipse(self.p, arc, reset=False)
        else:
            self.p.move_to(arc.end_point if ccw else arc.start_point)

    def draw_bulge(self, x: float, y: float, bulge: float):
        if self.pen_down and bulge:
            start_point = self.current_location
            scale = self.vector_length
            ccw = bulge > 0
            end_point = start_point + (x * scale, y * scale)
            bulge = abs(bulge) / 127.0
            if ccw:  # counter-clockwise
                center, start_angle, end_angle, radius = bulge_to_arc(
                    start_point, end_point, bulge
                )
            else:  # clockwise
                center, start_angle, end_angle, radius = bulge_to_arc(
                    end_point, start_point, bulge
                )
            self.draw_arc(center, radius, start_angle, end_angle, ccw=True)
        else:
            self.draw_displacement(x, y)


def decode_octant_specs(specs: int) -> Tuple[int, int, bool]:
    ccw = True
    if specs < 0:
        ccw = False
        specs = -specs
    start_octant = (specs >> 4) & 0xF
    octant_span = specs & 0xF
    return start_octant, octant_span, ccw
