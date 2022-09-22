import os
#os.environ["PYOPENGL_PLATFORM"] = "egl"
import numpy as np
import trimesh
import pyrender
import copy

import trimesh.transformations as tra

from util import render_utils

class SceneRenderer:
    def __init__(self, intrinsics=None, fov=np.pi / 6, caching=True, viewing_mode=False) -> None:
        """
        Class for rendering scenes.
        
        Arguments:
        ----------
        intrinsics {str} -- intrinsics of the camera. Can be one of the following:
            - 'kinect_azure', 'realsense', 'zivid2'
        fov {float} -- field of view of the camera
            - only used if intrinsics is None
        caching {bool} -- whether to cache loaded meshes
        viewing_mode {bool} -- whether to use pyrender.Viewer for rendering
        
        """

        self._fov = fov

        self._scene = pyrender.Scene()
        self._table_dims = [1.0, 1.2, 0.6]
        self._table_pose = np.eye(4)
        self._viewer = viewing_mode

        if viewing_mode:
            self._viewer = pyrender.Viewer(
                self._scene,
                use_raymond_lighting=True,
                run_in_thread=True)


        self._intrinsics = intrinsics
        if self._intrinsics == 'realsense':
            self._fx = 616.36529541  # X-axis focal length in pixels.
            self._fy = 616.20294189  # Y-axis focal length in pixels.
            self._cx = 310.25881958  # X-axis optical center in pixels.
            self._cy = 236.59980774  # Y-axis optical center in pixels.
            # The floating-point distance to the near clipping plane. If not specified, defaults to 0.05.
            self._znear = 0.04
            # The floating-point distance to the far clipping plane. zfar must be greater than znear. If not specified, defaults to 100.0.
            self._zfar = 20
            self._height = 480
            self._width = 640
        elif self._intrinsics == 'kinect_azure':
            self._fx = 631.54864502
            self._fy = 631.20751953
            self._cx = 638.43517329
            self._cy = 366.49904066
            self._znear = 0.04
            self._zfar = 20
            self._height = 720
            self._width = 1280
        elif self._intrinsics == 'zivid2':
            self._fx = 1783.0662841796875/4
            self._fy = 1782.3050537109375/4
            self._cx = 979.7349853515625/4
            self._cy = 610.2705688476562/4
            self._znear = 0.1
            self._zfar = 20
            self._height = 1200/4
            self._width = 1944/4

        self._add_table_node()
        self._init_camera_renderer()
        
        self._current_context = None
        self._cache = {} if caching else None
        self._caching = caching

    def _add_table_node(self):
        """
        Adds table mesh and sets pose
        """
        if self._viewer:
            return
        table_mesh = trimesh.creation.box(self._table_dims)
        mesh = pyrender.Mesh.from_trimesh(table_mesh)

        table_node = pyrender.Node(mesh=mesh, name='table')
        self._scene.add_node(table_node)
        self._scene.set_pose(table_node, self._table_pose)

    def _init_camera_renderer(self):
        """
        If not in visualizing mode, initialize camera with given intrinsics
        """

        if self._viewer:
            return

        if self._intrinsics in ['kinect_azure', 'realsense','zivid2']:
            camera = pyrender.IntrinsicsCamera(
                self._fx, self._fy, self._cx, self._cy, self._znear, self._zfar)
            self._camera_node = self._scene.add(
                camera, pose=np.eye(4), name='camera')
            # self.renderer = pyrender.OffscreenRenderer(viewport_width=self._width,
            #                                            viewport_height=self._height,
            #                                            point_size=1.0)
        else:
            camera = pyrender.PerspectiveCamera(
                yfov=self._fov, aspectRatio=1.0, znear=0.001, zfar = 10)  # do not change aspect ratio
            self._camera_node = self._scene.add(
                camera, pose=tra.euler_matrix(0, 0, 0), name='camera')
            self.renderer = pyrender.OffscreenRenderer(400, 400)

    def destroy(self):
        self.renderer.delete()

    def create(self):
        self.renderer = pyrender.OffscreenRenderer(viewport_width=self._width,
                                                   viewport_height=self._height,
                                                   point_size=1.0)

    def _load_object(self, path, scale = 1):
        """
        Load a mesh, scale and center it
        Arguments:
            path {str} -- path to mesh
            scale {float} -- scale of the mesh 
        Returns:
            dict -- contex with loaded mesh info
        """
        if (path, scale) in self._cache:
            return self._cache[(path, scale)]
        obj = render_utils.Object(path)
        obj.rescale(scale)

        tmesh = obj.mesh
        #tmesh_mean = np.mean(tmesh.vertices, 0)
        #tmesh_mean = 0
        #tmesh.vertices -= np.expand_dims(tmesh_mean, 0)

        lbs = np.min(tmesh.vertices, 0)
        ubs = np.max(tmesh.vertices, 0)
        object_distance = np.max(ubs - lbs) * 5

        mesh = pyrender.Mesh.from_trimesh(tmesh)
        name = os.path.splitext(os.path.basename(path))[0]

        context = {
            'name': name,
            'tmesh': copy.deepcopy(tmesh),
            'distance': object_distance,
            'node': pyrender.Node(mesh=mesh, name=name),
            #'mesh_mean': np.expand_dims(tmesh_mean, 0),
        }

        
        self._cache[(path, scale)] = context

        return self._cache[(path, scale)]

    def change_scene(self, obj_paths, obj_scales, obj_transforms):
        """Remove current objects and add new ones to the scene 
        Arguments:
            obj_paths {list} -- list of object mesh paths
            obj_scales {list} -- list of object scales
            obj_transforms {list} -- list of object transforms
        """
        if self._viewer:
            self._viewer.render_lock.acquire()
        for n in self._scene.get_nodes():
            if n.name not in ['table', 'camera', 'parent']:
                self._scene.remove_node(n)

        if not self._caching:
            self._cache = {}

        

        for p, t, s in zip(obj_paths, obj_transforms, obj_scales):

            object_context = self._load_object(p, s)
            object_context = copy.deepcopy(object_context)
            self._scene.add_node(object_context['node'])
            self._scene.set_pose(object_context['node'], t)


        if self._viewer:
            self._viewer.render_lock.release()

    
    def _to_pointcloud(self, depth):
        """Convert depth map to point cloud
        Arguments:
            depth {np.ndarray} -- HxW depth map
        Returns:
            np.ndarray -- Nx4 homog. point cloud
        """
        if self._intrinsics in ['kinect_azure', 'realsense', 'zivid2']:
            fy = self._fy
            fx = self._fx
            height = self._height
            width = self._width
            cx = self._cx
            cy = self._cy

            mask = np.where(depth > self._znear)

            x = mask[1]
            y = mask[0]

            normalized_x = (x.astype(np.float32) - cx)
            normalized_y = (y.astype(np.float32) - cy)
        else:
            fy = fx = 0.5 / np.tan(self._fov * 0.5)  # aspectRatio is one.
            height = depth.shape[0]
            width = depth.shape[1]

            mask = np.where(depth > 0)

            x = mask[1]
            y = mask[0]

            normalized_x = (x.astype(np.float32) - width * 0.5) / width
            normalized_y = (y.astype(np.float32) - height * 0.5) / height

        world_x = normalized_x * depth[y, x] / fx
        world_y = normalized_y * depth[y, x] / fy
        world_z = depth[y, x]
        ones = np.ones(world_z.shape[0], dtype=np.float32)

        return np.vstack((world_x, world_y, world_z, ones)).T

    def render(self, pose, render_pc=True, display = False):
        """Render object or scene in camera pose
        Arguments:
            pose {np.ndarray} -- 4x4 camera pose
        Keyword Arguments:
            render_pc {bool} -- whether to convert depth map to point cloud (default: {True})
        Returns:
            [np.ndarray, np.ndarray, np.ndarray, np.ndarray] -- HxWx3 color, HxW depth, Nx4 point cloud, 4x4 camera pose
        """

        transferred_pose = pose.copy()
        self._scene.set_pose(self._camera_node, transferred_pose)

        color, depth = self.renderer.render(self._scene)

        if render_pc:
            pc = self._to_pointcloud(depth)
        else:
            pc = None

        if display:
            render_utils.show_image(depth, None)

        return color, depth, pc, transferred_pose

    def render_labels(self, full_depth, obj_names, render_pc=False):
        """Render instance segmentation map
        Arguments:
            full_depth {np.ndarray} -- HxW depth map
            obj_paths {list} -- list of object paths in scene
            obj_scales {list} -- list of object scales in scene
        Keyword Arguments:
            render_pc {bool} -- whether to return object-wise point clouds (default: {False})
        Returns:
            [np.ndarray, list, dict] -- integer segmap with 0=background, list of 
                                        corresponding object names, dict of corresponding point clouds
        """

        scene_object_nodes = []
        for n in self._scene.get_nodes():
            if n.name not in ['camera', 'parent']:
                n.mesh.is_visible = False
                if n.name != 'table':
                    scene_object_nodes.append(n)

        

        pcs = []
        output = np.zeros(full_depth.shape, np.uint8)

        for n in scene_object_nodes:
            n.mesh.is_visible = True

            depth = self.renderer.render(self._scene)[1]
            mask = np.logical_and(
                (np.abs(depth - full_depth) < 1e-1), np.abs(full_depth) > 0
            )
            if not np.any(mask):
                continue
            # if np.any(output[mask] == 0):
            #     raise ValueError('wrong label')

            indices = [i+1 for i, x in enumerate(obj_names) if x == n.name]
            for i in indices:
                if not np.any(output == i):
                    output[mask] = i
                    break

            n.mesh.is_visible = False

            if render_pc:
                pcs.append(self._to_pointcloud(depth*mask))

        # Set so all objects are visible again
        for n in self._scene.get_nodes():
            if n.name not in ['camera', 'parent']:
                n.mesh.is_visible = True

        return output, obj_names, pcs

