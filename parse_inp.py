import os
from posixpath import abspath
import numpy as np
import argparse
import re
import utils


class InpParser:
    """
    Parse the Abaqus input file .inp
    """

    def __init__(self, inp_name):
        self.inp_name = inp_name

        self.nodes = None
        self.conn = None
        self.X = None
        self.groups = None

        self.SUPPORTED_ELEMENT = {
            "CPS3": {
                "nnode": 3,
                "vtk_type": 5,
                "note": "Three-node plane stress element",
            },
            "C3D8R": {
                "nnode": 8,
                "vtk_type": 12,
                "note": "general purpose linear brick element",
            },
            "C3D10": {
                "nnode": 10,
                "vtk_type": 24,
                "note": "Ten-node tetrahedral element",
            },
        }
        return

    def parse(self):
        """
        Parse the inp file.

        Return:
            nodes: nodal indices, (nnodes,)
            conn: a dictionary of connectivity, conn[element_type] = conn_array
            X: nodal locations, (nnodes, ndof_per_node)
            groups: grouped nodes, dictionary, groups[name] = [...]
        """
        # Get and clean data chunks
        data_chunks = self._load_data_chunks()
        self._clean_data(data_chunks)

        # Reorganize data chunk by type
        chunks_node = [c for c in data_chunks if c["data_chunk_type"].lower() == "node"]
        chunks_element = [
            c
            for c in data_chunks
            if c["data_chunk_type"].lower() == "element"
            and c["type"] in self.SUPPORTED_ELEMENT
        ]
        chunks_nset = [c for c in data_chunks if c["data_chunk_type"].lower() == "nset"]

        # Parse nodal location
        if len(chunks_node) > 1:
            print("[Warning] Multiple *Node sections detected")
            X = []
            for c in chunks_node:
                X.extend(c["data"])
        else:
            X = chunks_node[0]["data"]
        X = np.array(X)

        # Create nodal numbering
        nodes = np.arange(len(X))

        # Parse connectivity for different element types
        conn = {}
        for c in chunks_element:
            conn[c["type"]] = np.array(c["data"])

        # Parse grouped nodes
        groups = {}
        for c in chunks_nset:
            groups[c["nset"]] = np.array(c["data"])

        self.nodes = nodes
        self.conn = conn
        self.X = X
        self.groups = groups
        return nodes, conn, X, groups

    def to_vtk(self, nodal_sol={}, vtk_name=None):
        """
        Generate a vtk file for mesh and optionally solution

        Inputs:
            nodal_sol: nodal solution values, dictionary with the following
                        structure:

                        nodal_sol = {
                            "scalar1": [...],
                            "scalar2": [...],
                            ...
                        }
            vtk_name: name of the vtk
        """
        if self.nodes is None:
            self.parse()

        if vtk_name is None:
            vtk_name = "{:s}.vtk".format(os.path.splitext(self.inp_name)[0])
        utils.to_vtk(self.nodes, self.conn, self.X, nodal_sol, vtk_name)
        return

    def _sort_B_by_A(self, A, B):
        A, B = zip(*sorted(zip(A, B)))
        return A, B

    def _load_data_chunks(self):
        """
        Organize the entire text file to data chunks, each chunk has a header
        line and one or multiple data lines

        Return:
            data_chunks: list of dictionary, each dictionary has the following
                         structure:

                         data_chunk = {
                             "data_chunk_type": <type>,
                             "args": [<arg1>, <arg2>, ...],
                             <key1>: <value1>,
                             <key2>: <value2>,
                             ...
                             "data": [<dataline1>, <dataline2>, ...]
                         }

        """
        data_chunks = []

        # Patterns
        header_pattern = re.compile(r"\*(\w+)")  # \w = a-zA-Z0-9_
        args_pattern = re.compile(r"[\s,](\w+)(\s|,|$)")
        kwargs_pattern = re.compile(r"(\w+)=(\w+)")

        with open(self.inp_name, "r") as fh:
            for line in fh:
                line = line.strip()
                if header_pattern.search(line):
                    data_chunk_type = header_pattern.findall(line)[0]
                    args = args_pattern.findall(line)
                    kwargs = kwargs_pattern.findall(line)
                    chunk = {
                        "data_chunk_type": data_chunk_type,
                        "args": [a[0] for a in args],
                        "data": [],
                    }
                    for kw in kwargs:
                        key, value = kw
                        chunk[key.lower()] = value
                    data_chunks.append(chunk)
                elif data_chunks and line:
                    if line[0:2] != r"**":
                        data_chunks[-1]["data"].append(line)
        return data_chunks

    def _line_to_list(self, line, dtype=float, separate_index=False, offset=0):
        """
        Convert a line of string to list of numbers with mixed types

        Inputs:
            line: a line of text file
            dtype: python built-in data type

        Return (separete_index=True):
            index, vals
        Return (separete_index=False):
            vals
        """
        # Remove trailing space, comma and \n
        vals = line.strip("\n, ").split(",")
        vals = [dtype(v) + offset for v in vals]
        if separate_index:
            idx = vals[0]
            vals = vals[1:]
            return idx, vals
        return vals

    def _clean_data(self, data_chunks):
        """
        Convert data in data_chunks from list of strings to list of numerates

        Input/Output:
            data_chunks
        """
        for chunk in data_chunks:
            data_chunk_type = chunk["data_chunk_type"].lower()
            data = []
            raw_idx = []

            if data_chunk_type == "node":
                for line in chunk["data"]:
                    idx, xyz = self._line_to_list(
                        line, dtype=float, separate_index=True
                    )
                    raw_idx.append(idx)
                    data.append(xyz)

                # Make sure no duplicate nodal index and no skip, so that we can reorder
                assert len(set(raw_idx)) == len(data) == max(raw_idx) - min(raw_idx) + 1
                raw_idx, data = self._sort_B_by_A(raw_idx, data)

            elif data_chunk_type == "element":
                for line in chunk["data"]:
                    idx, elem_conn = self._line_to_list(
                        line, dtype=int, separate_index=True, offset=-1
                    )
                    raw_idx.append(idx)
                    data.append(elem_conn)

                # Make sure no duplicate element index and no skip, so that we can reorder
                assert len(set(raw_idx)) == len(data) == max(raw_idx) - min(raw_idx) + 1
                raw_idx, data = self._sort_B_by_A(raw_idx, data)

            elif data_chunk_type == "nset":
                for line in chunk["data"]:
                    data.extend(self._line_to_list(line, dtype=int, offset=-1))

            else:
                print(f"[Info] No cleaning rule for {data_chunk_type}")
                data = chunk["data"]

            # Update data
            chunk["data"] = data
        return


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("inp", type=str, metavar="[inp file]")
    args = p.parse_args()
    inp_parser = InpParser(args.inp)
    inp_parser.parse()
    inp_parser.to_vtk()
