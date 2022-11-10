from datetime import datetime
from typing import Iterable, Tuple, Union

import h5py
import numpy as np
import trimesh
from pymoab import core, types

# commented out to progress CI , I shall fix the versioning in another PR assigned @shimwell
# from ._version import __version__
__version__ = 0.1.7  #temp fix

def fix_normals(vertices, triangles_in_each_volume):

    fixed_triangles = []
    for triangles in triangles_in_each_volume:
        fixed_triangles.append(fix_normal(vertices, triangles))
    return fixed_triangles


def fix_normal(vertices, triangles):

    # for triangles in triangles_in_each_volume:
    mesh = trimesh.Trimesh(vertices=vertices, faces=triangles, process=False)

    mesh.fix_normals()

    return mesh.faces


def _define_moab_core_and_tags() -> Tuple[core.Core, dict]:
    """Creates a MOAB Core instance which can be built up by adding sets of
    triangles to the instance

    Returns:
        (pymoab Core): A pymoab.core.Core() instance
        (pymoab tag_handle): A pymoab.core.tag_get_handle() instance
    """

    # create pymoab instance
    moab_core = core.Core()

    tags = dict()

    sense_tag_name = "GEOM_SENSE_2"
    sense_tag_size = 2
    tags["surf_sense"] = moab_core.tag_get_handle(
        sense_tag_name,
        sense_tag_size,
        types.MB_TYPE_HANDLE,
        types.MB_TAG_SPARSE,
        create_if_missing=True,
    )

    tags["category"] = moab_core.tag_get_handle(
        types.CATEGORY_TAG_NAME,
        types.CATEGORY_TAG_SIZE,
        types.MB_TYPE_OPAQUE,
        types.MB_TAG_SPARSE,
        create_if_missing=True,
    )

    tags["name"] = moab_core.tag_get_handle(
        types.NAME_TAG_NAME,
        types.NAME_TAG_SIZE,
        types.MB_TYPE_OPAQUE,
        types.MB_TAG_SPARSE,
        create_if_missing=True,
    )

    tags["geom_dimension"] = moab_core.tag_get_handle(
        types.GEOM_DIMENSION_TAG_NAME,
        1,
        types.MB_TYPE_INTEGER,
        types.MB_TAG_DENSE,
        create_if_missing=True,
    )

    # Global ID is a default tag, just need the name to retrieve
    tags["global_id"] = moab_core.tag_get_handle(types.GLOBAL_ID_TAG_NAME)

    return moab_core, tags


def prepare_moab_core(
    moab_core,
    surface_id,
    volume_id,
    tags,
):

    surface_set = moab_core.create_meshset()
    volume_set = moab_core.create_meshset()

    # recent versions of MOAB handle this automatically
    # but best to go ahead and do it manually
    moab_core.tag_set_data(tags["global_id"], volume_set, volume_id)

    moab_core.tag_set_data(tags["global_id"], surface_set, surface_id)

    # set geom IDs
    moab_core.tag_set_data(tags["geom_dimension"], volume_set, 3)
    moab_core.tag_set_data(tags["geom_dimension"], surface_set, 2)

    # set category tag values
    moab_core.tag_set_data(tags["category"], volume_set, "Volume")
    moab_core.tag_set_data(tags["category"], surface_set, "Surface")

    # establish parent-child relationship
    moab_core.add_parent_child(volume_set, surface_set)

    # set surface sense
    sense_data = [volume_set, np.uint64(0)]
    moab_core.tag_set_data(tags["surf_sense"], surface_set, sense_data)

    return moab_core, surface_set, volume_set


def add_vertices_to_moab_core(moab_core, vertices, surface_set):

    moab_verts = moab_core.create_vertices(vertices)

    moab_core.add_entity(surface_set, moab_verts)
    return moab_core, moab_verts


def add_triangles_to_moab_core(
    material_tag, surface_set, moab_core, tags, triangle_groups, moab_verts, volume_set
):

    for triangle in triangle_groups:

        tri = (
            moab_verts[int(triangle[0])],
            moab_verts[int(triangle[1])],
            moab_verts[int(triangle[2])],
        )

        moab_triangle = moab_core.create_element(types.MBTRI, tri)
        moab_core.add_entity(surface_set, moab_triangle)

    group_set = moab_core.create_meshset()

    moab_core.tag_set_data(tags["category"], group_set, "Group")

    moab_core.tag_set_data(tags["name"], group_set, f"mat:{material_tag}")

    moab_core.tag_set_data(tags["geom_dimension"], group_set, 4)

    moab_core.add_entity(group_set, volume_set)

    return moab_core


def vertices_to_h5m(
    vertices: Union[
        Iterable[Tuple[float, float, float]], Iterable["cadquery.occ_impl.geom.Vector"]
    ],
    triangle_groups: Iterable[Tuple[int, int, int]],
    material_tags: Iterable[str],
    h5m_filename="dagmc.h5m",
    method="pymoab",
):
    """Converts vertices and triangle sets into a tagged h5m file compatible
    with DAGMC enabled neutronics simulations

    Args:
        vertices: an iterable of x,y,z coordinates
        triangle_groups: an iterable of triangle sets
        material_tags: the material names to tag the triangle sets with
        h5m_filename: the output h5m filename
        method: the method of creating the h5m file, either 'pymoab' or 'h5py'
    """

    if method == "h5py":
        vertices_to_h5m_h5py(
            vertices=vertices,
            triangle_groups=triangle_groups,
            material_tags=material_tags,
            h5m_filename=h5m_filename,
        )
    elif method == "pymoab":
        vertices_to_h5m_pymoab(
            vertices=vertices,
            triangle_groups=triangle_groups,
            material_tags=material_tags,
            h5m_filename=h5m_filename,
        )
    else:
        raise ValueError(f"method must be either pymoab or h5py, not {method}")


def vertices_to_h5m_h5py(
    vertices: Union[
        Iterable[Tuple[float, float, float]], Iterable["cadquery.occ_impl.geom.Vector"]
    ],
    triangle_groups: Iterable[Tuple[int, int, int]],
    material_tags: Iterable[str],
    h5m_filename="dagmc.h5m",
):

    vertices_floats = check_vertices(vertices)

    local_triangle_groups = fix_normals(
        vertices=vertices_floats, triangles_in_each_volume=triangle_groups
    )

    f = h5py.File(h5m_filename, "w")

    all_triangles = np.vstack(local_triangle_groups)

    # give each local group a unique tag
    # tag_data = np.concatenate(
    #     [np.full(len(group), i) for i, group in enumerate(local_triangle_groups)]
    # )
    # Alternative: Set all tags to -1
    tag_data = np.full(len(all_triangles), -1)

    tstt = f.create_group("tstt")

    # TODO don't hardcode
    tstt.attrs.create("max_id", np.uint(12))

    global_id = 1  # counts all entities

    # nodes group
    nodes = tstt.create_group("nodes")
    coords = nodes.create_dataset("coordinates", data=vertices)
    coords.attrs.create("start_id", global_id)
    global_id += len(vertices)
    # node tags are set further below, when
    # /tstt/tags/GLOBAL_ID/type is available

    # elements group
    elements = tstt.create_group("elements")
    elems = {
        "Edge": 1,
        "Tri": 2,
        "Quad": 3,
        "Polygon": 4,
        "Tet": 5,
        "Pyramid": 6,
        "Prism": 7,
        "Knife": 8,
        "Hex": 9,
        "Polyhedron": 10,
    }
    tstt["elemtypes"] = h5py.enum_dtype(elems)

    tri3_group = elements.create_group("Tri3")
    tri3_group.attrs.create("element_type", elems["Tri"], dtype=tstt["elemtypes"])

    connectivity_group = tri3_group.create_dataset(
        "connectivity",
        data=all_triangles + 1,  # node indices are 1 based in h5m
        dtype=np.uint64,
    )

    connectivity_group.attrs.create("start_id", global_id)
    global_id += len(all_triangles)

    tags_tri3_group = tri3_group.create_group("tags")
    tags_tri3_group.create_dataset("GLOBAL_ID", data=tag_data)

    # imitate "history" info from pymoab
    now = datetime.now()
    tstt.create_dataset(
        "history",
        data=[
            "vertices_to_h5m",
            __version__,
            now.strftime("%m/%d/%y"),
            now.strftime("%H:%M:%S"),
        ],
    )

    tstt_sets_group = tstt.create_group("sets")
    tstt_sets_group.create_dataset("children", data=[9], dtype=np.uint64)
    tstt_sets_group.create_dataset("contents", data=[1, 8, 10, 1, 11], dtype=np.uint64)
    lst = tstt_sets_group.create_dataset(
        "list",
        data=np.array(
            [
                [1, -1, 0, 10],
                [1, 0, 0, 2],
                [2, 0, 0, 2],
                [4, 0, 0, 10],
            ]
        ),
    )
    # TODO don't hardcode
    lst.attrs.create("start_id", 9)
    tstt_sets_group.create_dataset("parents", data=[10], dtype=np.uint64)
    tstt_sets_tags = tstt_sets_group.create_group("tags")

    tstt_tags_group = tstt.create_group("tags")

    cat_group = tstt_tags_group.create_group("CATEGORY")
    cat_group.attrs.create("class", 1, dtype=np.int32)
    cat_group.create_dataset("id_list", data=[9, 10, 11], dtype=np.uint64)
    arr = np.array(["Surface", "Volume", "Group"], dtype="|S32")
    cat_group["type"] = h5py.opaque_dtype(arr.dtype)
    cat_group["values"] = arr.astype(h5py.opaque_dtype(arr.dtype))

    diri_group = tstt_tags_group.create_group("DIRICHLET_SET")
    diri_group["type"] = np.dtype("i4")
    diri_group.attrs.create("class", 1, dtype=np.int32)
    diri_group.attrs.create("default", -1, dtype=diri_group["type"])
    diri_group.attrs.create("global", -1, dtype=diri_group["type"])

    geom_group = tstt_tags_group.create_group("GEOM_DIMENSION")
    geom_group["type"] = np.dtype("i4")
    geom_group.attrs.create("class", 1, dtype=np.int32)
    geom_group.attrs.create("default", -1, dtype=geom_group["type"])
    geom_group.attrs.create("global", -1, dtype=geom_group["type"])
    geom_group.create_dataset("id_list", data=[9, 10, 11], dtype=np.uint64)
    geom_group.create_dataset("values", data=[2, 3, 4], dtype=geom_group["type"])

    gsense_group = tstt_tags_group.create_group("GEOM_SENSE_2")
    gsense_group.attrs.create("class", 1, dtype=np.int32)
    gsense_group.attrs.create("is_handle", 1, dtype=np.int32)
    gsense_group.create_dataset("id_list", data=[9], dtype=np.uint64)
    # TODO
    # gsense_group["type"] = np.dtype("u8")
    # gsense_group.create_dataset("values", data=[10, 0], dtype=gsense_group["type"])

    gid_group = tstt_tags_group.create_group("GLOBAL_ID")
    gid_group["type"] = np.dtype("i4")
    gid_group.attrs.create("class", 2, dtype=np.int32)
    gid_group.attrs.create("default", -1, dtype=gid_group["type"])
    gid_group.attrs.create("global", -1, dtype=gid_group["type"])

    ms_group = tstt_tags_group.create_group("MATERIAL_SET")
    ms_group["type"] = np.dtype("i4")
    ms_group.attrs.create("class", 1, dtype=np.int32)
    ms_group.attrs.create("default", -1, dtype=ms_group["type"])
    ms_group.attrs.create("global", -1, dtype=ms_group["type"])

    name_group = tstt_tags_group.create_group("NAME")
    name_group.attrs.create("class", data=[1], dtype=np.int32)
    name_group.create_dataset(
        "id_list",
        data=[11],
        dtype=np.uint64,
    )
    arr = np.array(["mat:mat1"], dtype="|S32")
    name_group["type"] = h5py.opaque_dtype(arr.dtype)
    name_group["values"] = arr.astype(h5py.opaque_dtype(name_group["type"]))

    neumann_group = tstt_tags_group.create_group("NEUMANN_SET")
    neumann_group["type"] = np.dtype("i4")
    neumann_group.attrs.create("class", 1, dtype=np.int32)
    neumann_group.attrs.create("default", -1, dtype=neumann_group["type"])
    neumann_group.attrs.create("global", -1, dtype=neumann_group["type"])

    node_tags = nodes.create_group("tags")
    node_tags.create_dataset(
        "GLOBAL_ID",
        data=np.full(len(vertices), -1),
        dtype=gid_group["type"],
    )

    tstt_sets_tags.create_dataset(
        "GLOBAL_ID",
        data=[1, 1, -1, -1],
        dtype=gid_group["type"],
    )


def check_vertices(vertices):
    # limited attribute checking to see if user passed in a list of CadQuery vectors
    if (
        hasattr(vertices[0], "x")
        and hasattr(vertices[0], "y")
        and hasattr(vertices[0], "z")
    ):
        vertices_floats = []
        for vert in vertices:
            vertices_floats.append((vert.x, vert.y, vert.z))
    else:
        vertices_floats = vertices

    return vertices_floats


def vertices_to_h5m_pymoab(
    vertices: Union[
        Iterable[Tuple[float, float, float]], Iterable["cadquery.occ_impl.geom.Vector"]
    ],
    triangle_groups: Iterable[Tuple[int, int, int]],
    material_tags: Iterable[str],
    h5m_filename="dagmc.h5m",
):
    if len(material_tags) != len(triangle_groups):
        msg = f"The number of material_tags provided is {len(material_tags)} and the number of sets of triangles is {len(triangle_groups)}. You must provide one material_tag for every triangle set"
        raise ValueError(msg)

    vertices_floats = check_vertices(vertices)

    triangles = fix_normals(
        vertices=vertices_floats, triangles_in_each_volume=triangle_groups
    )

    moab_core, tags = _define_moab_core_and_tags()

    for vol_id, material_tag in enumerate(material_tags, 1):
        moab_core, surface_set, volume_set = prepare_moab_core(
            moab_core, surface_id=vol_id, volume_id=vol_id, tags=tags
        )

        moab_core, moab_verts = add_vertices_to_moab_core(
            moab_core, vertices_floats, surface_set
        )

        moab_core = add_triangles_to_moab_core(
            material_tag,
            surface_set,
            moab_core,
            tags,
            triangles[vol_id - 1],
            moab_verts,
            volume_set,
        )

    all_sets = moab_core.get_entities_by_handle(0)

    file_set = moab_core.create_meshset()

    moab_core.add_entities(file_set, all_sets)

    moab_core.write_file(h5m_filename)
