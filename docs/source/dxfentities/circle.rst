Circle
======

.. module:: ezdxf.entities
    :noindex:

CIRCLE (`DXF Reference`_) center at location :attr:`dxf.center` and radius of
:attr:`dxf.radius`. The CIRCLE entity has :ref:`OCS` coordinates.

.. seealso::

    - :ref:`tut_dxf_primitives`, section :ref:`tut_dxf_primitives_circle`
    - :class:`ezdxf.math.ConstructionCircle`
    - :ref:`Object Coordinate System`

======================== ==========================================
Subclass of              :class:`ezdxf.entities.DXFGraphic`
DXF type                 ``'CIRCLE'``
Factory function         :meth:`ezdxf.layouts.BaseLayout.add_circle`
Inherited DXF attributes :ref:`Common graphical DXF attributes`
======================== ==========================================

.. warning::

    Do not instantiate entity classes by yourself - always use the provided
    factory functions!

.. class:: Circle

    .. attribute:: dxf.center

        Center point of circle (2D/3D Point in :ref:`OCS`)

    .. attribute:: dxf.radius

        Radius of circle (float)

    .. automethod:: vertices

    .. automethod:: flattening

    .. automethod:: transform

    .. automethod:: translate

    .. automethod:: to_ellipse

    .. automethod:: to_spline

.. _DXF Reference: http://help.autodesk.com/view/OARX/2018/ENU/?guid=GUID-8663262B-222C-414D-B133-4A8506A27C18