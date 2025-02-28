Layer
=====

.. module:: ezdxf.entities
    :noindex:

LAYER (`DXF Reference`_) definition, defines attribute values for entities on
this layer for their attributes set to ``BYLAYER``.

======================== ==========================================
Subclass of              :class:`ezdxf.entities.DXFEntity`
DXF type                 ``'LAYER'``
Factory function         :meth:`Drawing.layers.new`
======================== ==========================================

.. seealso::

    Basic concepts of :ref:`layer_concept` and :ref:`tut_layers`

.. class:: Layer

    .. attribute:: dxf.handle

        DXF handle (feature for experts)

    .. attribute:: dxf.owner

        Handle to owner (:class:`~ezdxf.sections.table.LayerTable`).

    .. attribute:: dxf.name

        Layer name, case insensitive and can not contain any of this characters:
        ``<>/\":;?*|=``` (str)

    .. attribute:: dxf.flags

        Layer flags (bit-coded values, feature for experts)

        === ==========================================
        1   Layer is frozen; otherwise layer is thawed; use :meth:`is_frozen`,
            :meth:`freeze` and :meth:`thaw`
        2   Layer is frozen by default in new viewports
        4   Layer is locked; use :meth:`is_locked`, :meth:`lock`, :meth:`unlock`
        16  If set, table entry is externally dependent on an xref
        32  If both this bit and bit 16 are set, the externally dependent xref
            has been successfully resolved
        64  If set, the table entry was referenced by at least one entity in the
            drawing the last time the drawing was edited. (This flag is for the
            benefit of AutoCAD commands. It can be ignored by most programs that
            read DXF files and need not be set by programs that write DXF files)
        === ==========================================

    .. attribute:: dxf.color

        Layer color, but use property :attr:`Layer.color` to get/set color value,
        because color is negative for layer status `off` (int)

    .. attribute:: dxf.true_color

        Layer true color value as int, use property :attr:`Layer.rgb` to set/get
        true color value as (r, g, b) tuple.

        (requires DXF R2004)

    .. attribute:: dxf.linetype

        Name of line type (str)

    .. attribute:: dxf.plot

        Plot flag (int). Whether entities belonging to this layer should be drawn
        when the document is exported (plotted) to pdf. Does not affect
        visibility inside the CAD application itself.

        === ============================
        1   plot layer (default value)
        0   don't plot layer
        === ============================

    .. attribute:: dxf.lineweight

        Line weight in mm times 100 (e.g. 0.13mm = 13). Smallest line weight is
        13 and biggest line weight is 200, values outside this range prevents
        AutoCAD from loading the file.

        :code:`ezdxf.lldxf.const.LINEWEIGHT_DEFAULT` for using global default
        line weight.

        (requires DXF R13)

    .. attribute:: dxf.plotstyle_handle

        Handle to plot style name?

        (requires DXF R13)

    .. attribute:: dxf.material_handle

        Handle to default :class:`~ezdxf.entities.Material`.

        (requires DXF R13)

    .. attribute:: rgb

        Get/set DXF attribute :attr:`dxf.true_color` as (r, g, b) tuple,
        returns ``None`` if attribute :attr:`dxf.true_color` is not set.

        .. code-block:: python

            layer.rgb = (30, 40, 50)
            r, g, b = layer.rgb

        This is the recommend method to get/set RGB values, when ever possible
        do not use the DXF low level attribute :attr:`dxf.true_color`.

    .. attribute:: color

        Get/set layer color, preferred method for getting the layer color,
        because :attr:`dxf.color` is negative for layer status `off`.

    .. attribute:: description

        Get/set layer description as string

    .. attribute:: transparency

        Get/set layer transparency as float value in the range from 0 to 1.
        0 for no transparency (opaque) and 1 for 100% transparency.

    .. automethod:: is_frozen

    .. automethod:: freeze

    .. automethod:: thaw

    .. automethod:: is_locked

    .. automethod:: lock

    .. automethod:: unlock

    .. automethod:: is_off

    .. automethod:: is_on

    .. automethod:: on

    .. automethod:: off

    .. method:: get_color() -> int

        Use property :attr:`Layer.color` instead.

    .. method:: set_color(value: int) -> None

        Use property :attr:`Layer.color` instead.

    .. automethod:: rename

    .. automethod:: get_vp_overrides() -> LayerOverrides

LayerOverrides
--------------

.. class:: LayerOverrides

    This object stores the layer attribute overridden in :class:`Viewport`
    entities,  where each :class:`Viewport` can have individual layer attribute
    overrides.

    Layer attributes which can be overridden:

        - ACI color
        - true color (rgb)
        - linetype
        - lineweight
        - transparency

    Get the override object for a certain layer by the :meth:`Layer.get_vp_overrides`
    method.

    It is important to write changes back by calling :meth:`commit`, otherwise
    the changes are lost.

    .. important::

        The implementation of this feature as DXF structures is not documented
        by the DXF reference, so if you encounter problems or errors,
        **ALWAYS** provide the DXF files, otherwise it is not possible to help.

    .. automethod:: has_overrides

    .. automethod:: commit

    .. automethod:: get_color

    .. automethod:: set_color

    .. automethod:: get_rgb

    .. automethod:: set_rgb

    .. automethod:: get_transparency

    .. automethod:: set_transparency

    .. automethod:: get_linetype

    .. automethod:: set_linetype

    .. automethod:: get_lineweight

    .. automethod:: set_lineweight

    .. automethod:: discard


.. _DXF Reference: http://help.autodesk.com/view/OARX/2018/ENU/?guid=GUID-D94802B0-8BE8-4AC9-8054-17197688AFDB
