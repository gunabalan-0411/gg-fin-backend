"""A tiny in-memory stand-in for the Google Drive v3 `service` — just folder lookup / creation and uploads."""
import re


class _Call:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeDrive:
    def __init__(self):
        self.nodes = {"root": {"name": "My Drive", "parent": None, "folder": True}}
        self.uploads = []            # {"path", "name", "data", "mimetype", "media_type"}
        self.fail_upload_named = None  # substring of a file name whose upload should fail
        self._n = 0

    # service.files() -> self
    def files(self):
        return self

    def _path(self, folder_id):
        parts = []
        while folder_id and folder_id != "root":
            node = self.nodes[folder_id]
            parts.append(node["name"])
            folder_id = node["parent"]
        return "/".join(reversed(parts))

    def folders(self):
        return sorted(self._path(i) for i, n in self.nodes.items() if n["folder"] and i != "root")

    def list(self, q, fields=None, pageSize=None, orderBy=None):
        name = re.search(r"name='([^']*)'", q).group(1)
        parent = re.search(r"'([^']*)' in parents", q).group(1)

        def run():
            hits = [{"id": i} for i, n in self.nodes.items() if n["folder"] and n["name"] == name and n["parent"] == parent]
            return {"files": hits}
        return _Call(run)

    def create(self, body, media_body=None, fields=None):
        def run():
            self._n += 1
            fid = f"id{self._n}"
            parent = body["parents"][0]
            if media_body is None:                                   # a folder
                self.nodes[fid] = {"name": body["name"], "parent": parent, "folder": True}
                return {"id": fid}
            if self.fail_upload_named and self.fail_upload_named in body["name"]:
                raise RuntimeError("boom: Drive rejected the upload")
            data = media_body.getbytes(0, media_body.size())
            self.nodes[fid] = {"name": body["name"], "parent": parent, "folder": False}
            self.uploads.append({
                "path": self._path(parent), "name": body["name"], "data": data,
                "mimetype": media_body.mimetype(), "media_type": type(media_body).__name__,
            })
            return {"id": fid, "name": body["name"]}
        return _Call(run)
