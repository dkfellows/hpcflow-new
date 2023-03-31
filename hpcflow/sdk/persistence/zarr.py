from __future__ import annotations
from contextlib import contextmanager
import copy
from pathlib import Path

import shutil
import time
from typing import Any, Dict, Generator, Iterator, List, Optional, Tuple, Union
import numpy as np
import zarr
from numcodecs import MsgPack

from hpcflow.sdk.core.errors import WorkflowNotFoundError
from hpcflow.sdk.core.utils import (
    bisect_slice,
    ensure_in,
    get_in_container,
    get_md5_hash,
    get_relative_path,
    set_in_container,
)
from hpcflow.sdk.persistence import PersistentStore, dropbox_permission_err_retry
from hpcflow.sdk.typing import PathLike


def _encode_numpy_array(obj, type_lookup, path, root_group, arr_path):
    # Might need to generate new group:
    param_arr_group = root_group.require_group(arr_path)
    names = [int(i) for i in param_arr_group.keys()]
    if not names:
        new_idx = 0
    else:
        new_idx = max(names) + 1
    param_arr_group.create_dataset(name=f"arr_{new_idx}", data=obj)
    type_lookup["arrays"].append([path, new_idx])

    return None


def _decode_numpy_arrays(obj, type_lookup, path, arr_group, dataset_copy):
    for arr_path, arr_idx in type_lookup["arrays"]:
        try:
            rel_path = get_relative_path(arr_path, path)
        except ValueError:
            continue

        dataset = arr_group.get(f"arr_{arr_idx}")
        if dataset_copy:
            dataset = dataset[:]

        if rel_path:
            set_in_container(obj, rel_path, dataset)
        else:
            obj = dataset

    return obj


class ZarrPersistentStore(PersistentStore):
    """An efficient storage backend using Zarr that supports parameter-setting from
    multiple processes."""

    _param_grp_name = "parameter_data"
    _elem_grp_name = "element_data"
    _param_base_arr_name = "base"
    _param_sources_arr_name = "sources"
    _param_user_arr_grp_name = "arrays"
    _param_data_arr_grp_name = lambda _, param_idx: f"param_{param_idx}"
    _task_grp_name = lambda _, insert_ID: f"task_{insert_ID}"
    _task_elem_arr_name = "elements"
    _task_elem_iter_arr_name = "element_iters"

    _parameter_encoders = {np.ndarray: _encode_numpy_array}  # keys are types
    _parameter_decoders = {"arrays": _decode_numpy_arrays}  # keys are keys in type_lookup

    def __init__(self, workflow: Workflow) -> None:
        self._metadata = None  # cache used in `cached_load` context manager
        super().__init__(workflow)

    def exists(self) -> bool:
        try:
            self._get_root_group()
        except zarr.errors.PathNotFoundError:
            return False
        return True

    @property
    def has_pending(self) -> bool:
        """Returns True if there are pending changes that are not yet committed."""
        return any(bool(v) for k, v in self._pending.items() if k != "element_attrs")

    def _get_pending_dct(self) -> Dict:
        dct = super()._get_pending_dct()
        dct["element_attrs"] = {}  # keys are task indices
        dct["element_iter_attrs"] = {}  # keys are task indices
        dct["EAR_attrs"] = {}  # keys are task indices
        dct["parameter_data"] = 0  # keep number of pending data rather than indices
        return dct

    @classmethod
    def write_empty_workflow(
        cls,
        template_js: Dict,
        template_components_js: Dict,
        path: Path,
        overwrite: bool,
    ) -> None:

        replaced_file = None
        if path.exists():
            if overwrite:
                replaced_file = cls._rename_existing(path)
            else:
                raise ValueError(f"Path already exists: {path}.")

        metadata = {
            "template": template_js,
            "template_components": template_components_js,
            "num_added_tasks": 0,
            "loops": [],
        }
        if replaced_file:
            metadata["replaced_file"] = str(replaced_file.name)

        store = zarr.DirectoryStore(path)
        root = zarr.group(store=store, overwrite=overwrite)
        root.attrs.update(metadata)

        root.create_group(name=cls._elem_grp_name)
        parameter_data = root.create_group(name=cls._param_grp_name)
        parameter_data.create_dataset(
            name=cls._param_base_arr_name,
            shape=0,
            dtype=object,
            object_codec=MsgPack(),
            chunks=1,
        )
        parameter_data.create_dataset(
            name=cls._param_sources_arr_name,
            shape=0,
            dtype=object,
            object_codec=MsgPack(),
            chunks=1000,  # TODO: check this is a sensible size with many parameters
        )
        parameter_data.create_group(name=cls._param_user_arr_grp_name)

    def load_metadata(self):
        return self._metadata or self._load_metadata()

    def _load_metadata(self):
        return self._get_root_group(mode="r").attrs.asdict()

    @contextmanager
    def cached_load(self) -> Iterator[Dict]:
        """Context manager to cache the root attributes (i.e. metadata)."""
        if self._metadata:
            yield
        else:
            try:
                self._metadata = self._load_metadata()
                yield
            finally:
                self._metadata = None

    def _get_root_group(self, mode: str = "r") -> zarr.Group:
        return zarr.open(self.workflow.path, mode=mode)

    def _get_parameter_group(self, mode: str = "r") -> zarr.Group:
        return self._get_root_group(mode=mode).get(self._param_grp_name)

    def _get_parameter_base_array(self, mode: str = "r") -> zarr.Array:
        return self._get_parameter_group(mode=mode).get(self._param_base_arr_name)

    def _get_parameter_sources_array(self, mode: str = "r") -> zarr.Array:
        return self._get_parameter_group(mode=mode).get(self._param_sources_arr_name)

    def _get_parameter_user_array_group(self, mode: str = "r") -> zarr.Group:
        return self._get_parameter_group(mode=mode).get(self._param_user_arr_grp_name)

    def _get_parameter_data_array_group(
        self,
        parameter_idx: int,
        mode: str = "r",
    ) -> zarr.Group:
        return self._get_parameter_user_array_group(mode=mode).get(
            self._param_data_arr_grp_name(parameter_idx)
        )

    def _get_element_group(self, mode: str = "r") -> zarr.Group:
        return self._get_root_group(mode=mode).get(self._elem_grp_name)

    def _get_task_group_path(self, insert_ID: int) -> str:
        return self._task_grp_name(insert_ID)

    def _get_task_group(self, insert_ID: int, mode: str = "r") -> zarr.Group:
        return self._get_element_group(mode=mode).get(self._task_grp_name(insert_ID))

    def _get_task_elements_array(self, insert_ID: int, mode: str = "r") -> zarr.Array:
        return self._get_task_group(insert_ID, mode=mode).get(self._task_elem_arr_name)

    def _get_task_elem_iters_array(self, insert_ID: int, mode: str = "r") -> zarr.Array:
        return self._get_task_group(insert_ID, mode=mode).get(
            self._task_elem_iter_arr_name
        )

    def _get_task_element_attrs(self, task_idx: int, task_insert_ID: int) -> Dict:
        if task_idx in self._pending["element_attrs"]:
            attrs = self._pending["element_attrs"][task_idx]
        elif task_idx in self._pending["tasks"]:
            # the task is new and not yet committed
            attrs = self._get_element_array_empty_attrs()
        else:
            attrs = self._get_task_elements_array(task_insert_ID, mode="r").attrs
            attrs = attrs.asdict()
        return attrs

    def _get_task_element_iter_attrs(self, task_idx: int, task_insert_ID: int) -> Dict:
        if task_idx in self._pending["element_iter_attrs"]:
            attrs = self._pending["element_iter_attrs"][task_idx]
        elif task_idx in self._pending["tasks"]:
            # the task is new and not yet committed
            attrs = self._get_element_iter_array_empty_attrs()
        else:
            attrs = self._get_task_elem_iters_array(task_insert_ID, mode="r").attrs
            attrs = attrs.asdict()
        return attrs

    def add_elements(
        self,
        task_idx: int,
        task_insert_ID: int,
        elements: List[Dict],
        element_iterations: List[Dict],
    ) -> None:

        attrs_original = self._get_task_element_attrs(task_idx, task_insert_ID)
        elements, attrs = self._compress_elements(elements, attrs_original)
        if attrs != attrs_original:
            if task_idx not in self._pending["element_attrs"]:
                self._pending["element_attrs"][task_idx] = {}
            self._pending["element_attrs"][task_idx].update(attrs)

        iter_attrs_original = self._get_task_element_iter_attrs(task_idx, task_insert_ID)
        element_iters, iter_attrs = self._compress_element_iters(
            element_iterations, iter_attrs_original
        )
        if iter_attrs != iter_attrs_original:
            if task_idx not in self._pending["element_iter_attrs"]:
                self._pending["element_iter_attrs"][task_idx] = {}
            self._pending["element_iter_attrs"][task_idx].update(iter_attrs)

        return super().add_elements(task_idx, task_insert_ID, elements, element_iters)

    def add_EARs(
        self,
        task_idx: int,
        task_insert_ID: int,
        element_iter_idx,
        EARs,
    ) -> None:
        iter_attrs_original = self._get_task_element_iter_attrs(task_idx, task_insert_ID)
        EARs, iter_attrs = self._compress_EARs(EARs, iter_attrs_original)
        if iter_attrs != iter_attrs_original:
            if task_idx not in self._pending["element_iter_attrs"]:
                self._pending["element_iter_attrs"][task_idx] = {}
            self._pending["element_iter_attrs"][task_idx].update(iter_attrs)

        key = (task_idx, task_insert_ID, element_iter_idx)
        if key not in self._pending["EARs"]:
            self._pending["EARs"][key] = []
        self._pending["EARs"][key].extend(EARs)
        self.save()

    def _compress_elements(self, elements: List, attrs: Dict) -> Tuple[List, Dict]:
        """Split element data into lists of integers and lookup lists to effectively
        compress the data.

        See also: `_decompress_elements` for the inverse operation.

        """

        attrs = copy.deepcopy(attrs)
        compressed = []
        for elem in elements:
            seq_idx = [
                [ensure_in(k, attrs["sequences"]), v] for k, v in elem["seq_idx"].items()
            ]
            compressed.append(
                [
                    elem["iterations_idx"],
                    elem["es_idx"],
                    seq_idx,
                ]
            )
        return compressed, attrs

    def _compress_element_iters(
        self, element_iters: List, attrs: Dict
    ) -> Tuple[List, Dict]:
        """Split element iteration data into lists of integers and lookup lists to
        effectively compress the data.

        See also: `_decompress_element_iters` for the inverse operation.

        """

        attrs = copy.deepcopy(attrs)
        compressed = []
        for iter_i in element_iters:
            loop_idx = [
                [ensure_in(k, attrs["loops"]), v] for k, v in iter_i["loop_idx"].items()
            ]
            schema_params = [
                ensure_in(k, attrs["schema_parameters"])
                for k in iter_i["schema_parameters"]
            ]
            data_idx = [
                [ensure_in(dk, attrs["parameter_paths"]), dv]
                for dk, dv in iter_i["data_idx"].items()
            ]

            EARs, attrs = self._compress_EARs(iter_i["actions"], attrs)
            compact = [
                iter_i["global_idx"],
                data_idx,
                int(iter_i["EARs_initialised"]),
                schema_params,
                loop_idx,
                EARs,
            ]
            compressed.append(compact)
        return compressed, attrs

    def _compress_EARs(self, EARs: Dict, attrs: Dict) -> List:
        """Split EAR data into lists of integers and lookup lists to effectively compress
        the data.

        See also: `_decompress_EARs` for the inverse operation.

        """
        attrs = copy.deepcopy(attrs)
        compressed = []
        for act_idx, runs in EARs.items():
            act_run_i = [
                act_idx,
                [
                    [
                        [ensure_in(dk, attrs["parameter_paths"]), dv]
                        for dk, dv in r["data_idx"].items()
                    ]
                    for r in runs
                ],
            ]
            compressed.append(act_run_i)
        return compressed, attrs

    def _decompress_elements(self, elements: List, attrs: Dict) -> List:

        out = []
        for elem in elements:
            elem_i = {
                "iterations_idx": elem[0],
                "es_idx": elem[1],
                "seq_idx": {attrs["sequences"][k]: v for (k, v) in elem[2]},
            }
            out.append(elem_i)
        return out

    def _decompress_element_iters(self, element_iters: List, attrs: Dict) -> List:
        out = []
        for iter_i in element_iters:
            iter_i_decomp = {
                "global_idx": iter_i[0],
                "data_idx": {attrs["parameter_paths"][k]: v for (k, v) in iter_i[1]},
                "EARs_initialised": bool(iter_i[2]),
                "schema_parameters": [attrs["schema_parameters"][k] for k in iter_i[3]],
                "loop_idx": {attrs["loops"][k]: v for (k, v) in iter_i[4]},
                "actions": self._decompress_EARs(iter_i[5], attrs),
            }
            out.append(iter_i_decomp)
        return out

    def _decompress_EARs(self, EARs: List, attrs: Dict) -> List:
        out = {
            act_idx: [
                {
                    "data_idx": {attrs["parameter_paths"][k]: v for (k, v) in data_idx},
                }
                for data_idx in runs
            ]
            for (act_idx, runs) in EARs
        }
        return out

    @staticmethod
    def _get_element_array_empty_attrs() -> Dict:
        return {"sequences": []}

    @staticmethod
    def _get_element_iter_array_empty_attrs() -> Dict:
        return {
            "loops": [],
            "schema_parameters": [],
            "parameter_paths": [],
        }

    def _get_zarr_store(self):
        return self._get_root_group().store

    def _remove_pending_parameter_data(self) -> None:
        """Delete pending parameter data from disk."""
        base = self._get_parameter_base_array(mode="r+")
        sources = self._get_parameter_sources_array(mode="r+")
        for param_idx in range(self._pending["parameter_data"], 0, -1):
            grp = self._get_parameter_data_array_group(param_idx - 1)
            if grp:
                zarr.storage.rmdir(store=self._get_zarr_store(), path=grp.path)
        base.resize(base.size - self._pending["parameter_data"])
        sources.resize(sources.size - self._pending["parameter_data"])

    def reject_pending(self) -> None:
        if self._pending["parameter_data"]:
            self._remove_pending_parameter_data()
        super().reject_pending()

    def commit_pending(self) -> None:

        md = self.load_metadata()

        # merge new tasks:
        for task_idx, task_js in self._pending["template_tasks"].items():
            md["template"]["tasks"].insert(task_idx, task_js)  # TODO should be index?

        # write new workflow tasks to disk:
        for task_idx, _ in self._pending["tasks"].items():

            insert_ID = self._pending["template_tasks"][task_idx]["insert_ID"]
            task_group = self._get_element_group(mode="r+").create_group(
                self._get_task_group_path(insert_ID)
            )
            element_arr = task_group.create_dataset(
                name=self._task_elem_arr_name,
                shape=0,
                dtype=object,
                object_codec=MsgPack(),
                chunks=1000,  # TODO: check this is a sensible size with many elements
            )
            element_arr.attrs.update(self._get_element_array_empty_attrs())
            element_iters_arr = task_group.create_dataset(
                name=self._task_elem_iter_arr_name,
                shape=0,
                dtype=object,
                object_codec=MsgPack(),
                chunks=1000,  # TODO: check this is a sensible size with many elements
            )
            element_iters_arr.attrs.update(self._get_element_iter_array_empty_attrs())
            md["num_added_tasks"] += 1

        # merge new template components:
        self._merge_pending_template_components(md["template_components"])

        # merge new element sets:
        for task_idx, es_js in self._pending["element_sets"].items():
            md["template"]["tasks"][task_idx]["element_sets"].extend(es_js)

        # write new elements to disk:
        for (task_idx, insert_ID), elements in self._pending["elements"].items():
            elem_arr = self._get_task_elements_array(insert_ID, mode="r+")
            elem_arr_add = np.empty((len(elements)), dtype=object)
            elem_arr_add[:] = elements
            elem_arr.append(elem_arr_add)
            if task_idx in self._pending["element_attrs"]:
                elem_arr.attrs.put(self._pending["element_attrs"][task_idx])

        for (_, insert_ID), iters_idx in self._pending["element_iterations_idx"].items():
            elem_arr = self._get_task_elements_array(insert_ID, mode="r+")
            for elem_idx, iters_idx_i in iters_idx.items():
                elem_dat = elem_arr[elem_idx]
                elem_dat[0] += iters_idx_i
                elem_arr[elem_idx] = elem_dat

        # commit new element iterations:
        for (task_idx, insert_ID), element_iters in self._pending[
            "element_iterations"
        ].items():
            elem_iter_arr = self._get_task_elem_iters_array(insert_ID, mode="r+")
            elem_iter_arr_add = np.empty(len(element_iters), dtype=object)
            elem_iter_arr_add[:] = element_iters
            elem_iter_arr.append(elem_iter_arr_add)
            if task_idx in self._pending["element_iter_attrs"]:
                elem_iter_arr.attrs.put(self._pending["element_iter_attrs"][task_idx])

        # commit new element iteration loop indices:
        for (_, insert_ID, iters_idx_i), loop_idx_i in self._pending["loop_idx"].items():
            elem_iter_arr = self._get_task_elem_iters_array(insert_ID, mode="r+")
            iter_dat = elem_iter_arr[iters_idx_i]
            iter_dat[4].extend(loop_idx_i)
            elem_iter_arr[iters_idx_i] = iter_dat

        # commit new element iteration EARs:
        for (_, insert_ID, iters_idx_i), actions_i in self._pending["EARs"].items():
            elem_iter_arr = self._get_task_elem_iters_array(insert_ID, mode="r+")
            iter_dat = elem_iter_arr[iters_idx_i]
            iter_dat[5].extend(actions_i)
            iter_dat[2] = int(True)  # EARs_initialised
            elem_iter_arr[iters_idx_i] = iter_dat

        # commit new loops:
        md["template"]["loops"].extend(self._pending["template_loops"])

        # commit new workflow loops:
        md["loops"].extend(self._pending["loops"])

        if self._pending["remove_replaced_file_record"]:
            del md["replaced_file"]

        # TODO: maybe clear pending keys individually, so if there is an error we can
        # retry/continue with committing?

        # commit updated metadata:
        self._get_root_group(mode="r+").attrs.put(md)
        self.clear_pending()

    def _get_persistent_template_components(self) -> Dict:
        return self.load_metadata()["template_components"]

    def get_template(self) -> Dict:
        # No need to consider pending; this is called once per Workflow object
        return self.load_metadata()["template"]

    def get_loops(self) -> List[Dict]:
        # No need to consider pending; this is called once per Workflow object
        return self.load_metadata()["loops"]

    def get_num_added_tasks(self) -> int:
        return self.load_metadata()["num_added_tasks"] + len(self._pending["tasks"])

    def get_all_tasks_metadata(self) -> List[Dict]:
        out = []
        for _, grp in self._get_element_group().groups():
            elem_arr = grp.get(self._task_elem_arr_name)
            out.append({"num_elements": len(elem_arr)})
        return out

    def get_task_elements(
        self,
        task_idx: int,
        task_insert_ID: int,
        selection: slice,
        keep_iterations_idx: bool = False,
    ) -> List:

        task = self.workflow.tasks[task_idx]
        num_pers = task._num_elements
        num_iter_pers = task._num_element_iterations
        pers_slice, pend_slice = bisect_slice(selection, num_pers)
        pers_range = range(pers_slice.start, pers_slice.stop, pers_slice.step)

        elem_iter_arr = None
        if len(pers_range):
            elem_arr = self._get_task_elements_array(task_insert_ID)
            elem_iter_arr = self._get_task_elem_iters_array(task_insert_ID)
            try:
                elements = list(elem_arr[pers_slice])
            except zarr.errors.NegativeStepError:
                elements = [elem_arr[idx] for idx in pers_range]
        else:
            elements = []

        key = (task_idx, task_insert_ID)
        if key in self._pending["elements"]:
            elements += self._pending["elements"][key][pend_slice]

        # add iterations:
        sel_range = range(selection.start, selection.stop, selection.step)
        iterations = {}
        for element_idx, element in zip(sel_range, elements):

            # find which iterations to add:
            iters_idx = element[0]

            # include pending iterations:
            if key in self._pending["element_iterations_idx"]:
                iters_idx += self._pending["element_iterations_idx"][key][element_idx]

            # populate new iterations list:
            for iter_idx_i in iters_idx:
                if iter_idx_i + 1 > num_iter_pers:
                    i_pending = iter_idx_i - num_iter_pers
                    iter_i = copy.deepcopy(
                        self._pending["element_iterations"][key][i_pending]
                    )
                else:
                    iter_i = elem_iter_arr[iter_idx_i]

                # include pending EARs:
                EARs_key = (task_idx, task_insert_ID, iter_idx_i)
                if EARs_key in self._pending["EARs"]:
                    iter_i[5].extend(self._pending["EARs"][EARs_key])

                # include pending loops:
                loop_idx_key = (task_idx, task_insert_ID, iter_idx_i)
                if loop_idx_key in self._pending["loop_idx"]:
                    iter_i[4].extend(self._pending["loop_idx"][loop_idx_key])

                iterations[iter_idx_i] = iter_i

        elements = self._decompress_elements(elements, self._get_task_element_attrs(*key))

        iters_k, iters_v = zip(*iterations.items())
        attrs = self._get_task_element_iter_attrs(*key)
        iters_v = self._decompress_element_iters(iters_v, attrs)
        elem_iters = dict(zip(iters_k, iters_v))

        for elem_idx, elem_i in zip(sel_range, elements):
            elem_i["iterations"] = [elem_iters[i] for i in elem_i["iterations_idx"]]
            if not keep_iterations_idx:
                del elem_i["iterations_idx"]
            elem_i["index"] = elem_idx

        return elements

    def _encode_parameter_data(
        self,
        obj: Any,
        root_group: zarr.Group,
        arr_path: str,
        path: List = None,
        type_lookup: Optional[Dict] = None,
    ) -> Dict[str, Any]:

        return super()._encode_parameter_data(
            obj=obj,
            path=path,
            type_lookup=type_lookup,
            root_group=root_group,
            arr_path=arr_path,
        )

    def _decode_parameter_data(
        self,
        data: Union[None, Dict],
        arr_group: zarr.Group,
        path: Optional[List[str]] = None,
        dataset_copy=False,
    ) -> Any:

        return super()._decode_parameter_data(
            data=data,
            path=path,
            arr_group=arr_group,
            dataset_copy=dataset_copy,
        )

    def _add_parameter_data(self, data: Any, source: Dict) -> int:

        base_arr = self._get_parameter_base_array(mode="r+")
        sources = self._get_parameter_sources_array(mode="r+")
        idx = base_arr.size

        if data is not None:
            data = self._encode_parameter_data(
                obj=data["data"],
                root_group=self._get_parameter_user_array_group(mode="r+"),
                arr_path=self._param_data_arr_grp_name(idx),
            )

        base_arr.append([data])
        sources.append([source])
        self._pending["parameter_data"] += 1
        self.save()

        return idx

    def set_parameter(self, index: int, data: Any) -> None:
        """Set the value of a pre-allocated parameter."""

        if self.is_parameter_set(index):
            raise RuntimeError(f"Parameter at index {index} is already set!")

        base_arr = self._get_parameter_base_array(mode="r+")
        base_arr[index] = self._encode_parameter_data(
            obj=data,
            root_group=self._get_parameter_user_array_group(mode="r+"),
            arr_path=self._param_data_arr_grp_name(index),
        )

    def _get_parameter_data(self, index: int) -> Any:
        return self._get_parameter_base_array(mode="r")[index]

    def get_parameter_data(self, index: int) -> Tuple[bool, Any]:
        data = self._get_parameter_data(index)
        is_set = False if data is None else True
        data = self._decode_parameter_data(
            data=data,
            arr_group=self._get_parameter_data_array_group(index),
        )
        return (is_set, data)

    def get_parameter_source(self, index: int) -> Dict:
        return self._get_parameter_sources_array(mode="r")[index]

    def get_all_parameter_data(self) -> Dict[int, Any]:
        max_key = self._get_parameter_base_array(mode="r").size - 1
        out = {}
        for idx in range(max_key + 1):
            out[idx] = self.get_parameter_data(idx)
        return out

    def is_parameter_set(self, index: int) -> bool:
        return self._get_parameter_data(index) is not None

    def check_parameters_exist(
        self, indices: Union[int, List[int]]
    ) -> Union[bool, List[bool]]:
        is_multi = True
        if not isinstance(indices, (list, tuple)):
            is_multi = False
            indices = [indices]
        base = self._get_parameter_base_array(mode="r")
        idx_range = range(base.size)
        exists = [i in idx_range for i in indices]
        if not is_multi:
            exists = exists[0]
        return exists

    def _init_task_loop(
        self,
        task_idx: int,
        task_insert_ID: int,
        element_sel: slice,
        name: str,
    ) -> None:
        """Initialise the zeroth iteration of a named loop for a specified task."""

        elements = self.get_task_elements(
            task_idx=task_idx,
            task_insert_ID=task_insert_ID,
            selection=element_sel,
            keep_iterations_idx=True,
        )

        attrs_original = self._get_task_element_iter_attrs(task_idx, task_insert_ID)
        attrs = copy.deepcopy(attrs_original)
        for element in elements:
            for iter_idx, iter_i in zip(element["iterations_idx"], element["iterations"]):

                if name in (attrs["loops"][k] for k in iter_i[4]):
                    raise ValueError(f"Loop {name!r} already initialised!")

                key = (task_idx, task_insert_ID, iter_idx)
                if key not in self._pending["loop_idx"]:
                    self._pending["loop_idx"][key] = []

                self._pending["loop_idx"][key].append(
                    [ensure_in(name, attrs["loops"]), 0]
                )

        if attrs != attrs_original:
            if task_idx not in self._pending["element_iter_attrs"]:
                self._pending["element_iter_attrs"][task_idx] = {}
            self._pending["element_iter_attrs"][task_idx].update(attrs)

    @dropbox_permission_err_retry
    def delete_no_confirm(self) -> None:
        """Permanently delete the workflow data with no confirmation."""
        # Dropbox (on Windows, at least) seems to try to re-sync some of the workflow
        # files if it is deleted soon after creation, which is the case on a failed
        # workflow creation (e.g. missing inputs):
        while self.workflow.path.is_dir():
            shutil.rmtree(self.workflow.path)
            time.sleep(0.5)

    @dropbox_permission_err_retry
    def remove_replaced_file(self) -> None:
        md = self.load_metadata()
        if "replaced_file" in md:
            shutil.rmtree(Path(md["replaced_file"]))
            self._pending["remove_replaced_file_record"] = True
            self.save()

    @dropbox_permission_err_retry
    def reinstate_replaced_file(self) -> None:
        print(f"reinstate replaced file!")
        md = self.load_metadata()
        if "replaced_file" in md:
            # TODO does rename work for the dir?
            Path(md["replaced_file"]).rename(self.path)

    def copy(self, path: PathLike = None) -> None:
        shutil.copytree(self.path, path)

    def is_modified_on_disk(self) -> bool:
        if self._metadata:
            return get_md5_hash(self._load_metadata()) != get_md5_hash(self._metadata)
        else:
            # nothing to compare to
            return False
