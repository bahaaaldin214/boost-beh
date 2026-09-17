import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

class PathLogic:

    def __init__(self, system):
        # Prefer env so vosslink / Windows / Argon mounts work without code edits.
        # Default = Voss lab Linux LSS (not the stale /mnt/lss shortcut).
        # Windows example: BOOST_LSS_ROOT=Z:\Projects\BOOST
        self.base_path = os.environ.get(
            "BOOST_LSS_ROOT", "/mnt/nfs/lss/vosslabhpc/Projects/BOOST"
        )
        self.sites = ["UI", "NE"]
        self.studies = ["int", "obs"]
        self.obs_path = os.path.join(self.base_path, "ObservationalStudy/3-Experiment/data")
        self.int_path = os.path.join(self.base_path, "InterventionStudy/3-Experiment/data")
        self.data_path = "../../data"  # source root
        self.system = system

    # ---------- helpers ----------
    @staticmethod
    def _first_digit(s: str) -> str:
        for ch in s:
            if ch.isdigit():
                return ch
        return ""

    @staticmethod
    def _basename(p: str) -> str:
        return os.path.basename(p.rstrip(os.sep))

    # ---------- indexing (single pass over filesystem) ----------
    def index_subject_sources(self):
        """
        Build {sub_name: src_path} by scanning once across all study/site roots.
        Handles dirs named 'sub-7057' or '7057'.
        """
        index = {}
        for study in self.studies:
            for site in self.sites:
                root = os.path.join(self.data_path, study, site)
                if not os.path.isdir(root):
                    continue
                # os.scandir is faster than listdir+isdir
                with os.scandir(root) as it:
                    for entry in it:
                        if entry.is_dir(follow_symlinks=False) and (
                            entry.name.startswith("sub") or entry.name[0].isdigit()
                        ):
                            index[entry.name] = entry.path
        return index  # e.g., {'sub-7057': '../../data/obs/UI/sub-7057', ...}

    # ---------- build targets (robust to naming) ----------
    def build_target_paths(self, subs):
        """
        Return {sub_name: dst_beh_path}. Places 7* into obs, 8*/9* into int.
        Works for '7057' or 'sub-7057' (extracts first digit).
        """
        targets = {}
        for raw in subs:
            s = raw if isinstance(raw, str) else str(raw)
            d = self._first_digit(s)
            if not d:
                continue  # skip if no digit present
            if d == "7":
                dst = os.path.join(self.obs_path, s, "beh")
            elif d in ("8", "9"):
                dst = os.path.join(self.int_path, s, "beh")
            else:
                continue  # ignore anything else
            targets[s] = dst
        return targets  # { 'sub-7057': '.../ObservationalStudy/.../sub-7057/beh', ... }

    # (kept for API compatibility)
    #def build_out_paths(self, subs):
    #   targets = self.build_target_paths(subs)
    #   obs_list = [p for s, p in targets.items() if self._first_digit(s) == "7"]
    #   int_list = [p for s, p in targets.items() if self._first_digit(s) in ("8","9")]
    #   return {"obs": obs_list, "int": int_list}

    # ---------- copy logic ----------
    @staticmethod
    def _copy_tree_merge(src: str, dst: str):
        """
        Copy entire src tree into dst (merge if exists).
        Uses shutil.copytree for directories; dirs_exist_ok=True for merging.
        """
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        # copytree will create dst and merge when dirs_exist_ok=True
        shutil.copytree(src, dst, dirs_exist_ok=True)

    def copy_subjects_to_server(self, max_workers: int = None, dry_run: bool = False):
        """
        Heavy-duty, optimized copy:
          1) Index sources once: {sub: src}
          2) Compute targets once: {sub: dst}
          3) Parallel copy with threads (IO-bound)

        Returns (succeeded, failed) subject name lists.
        """
        # 1) index all subjects (source paths)
        src_index = self.index_subject_sources()
        if not src_index:
            return [], []

        # 2) build targets for *those* subjects
        targets = self.build_target_paths(src_index.keys())
        if not targets:
            return [], []

        # 3) plan: list of (sub, src, dst)
        jobs = []
        for sub, src in src_index.items():
            dst = targets.get(sub)
            if dst:
                jobs.append((sub, src, dst))

        if dry_run:
            # quick summary without copying
            return [sub for sub, _, _ in jobs], []

        # Choose a sensible default for IO-bound tasks
        if max_workers is None:
            # Many small files benefit from more threads; cap to avoid oversubscription
            cpu = os.cpu_count() or 4
            max_workers = min(32, cpu * 5)

        succeeded, failed = [], []
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(self._copy_tree_merge, src, dst): (sub, src, dst)
                for (sub, src, dst) in jobs
            }
            for fut in as_completed(futures):
                sub, src, dst = futures[fut]
                try:
                    fut.result()
                    succeeded.append(sub)
                except Exception:
                    failed.append(sub)
        return succeeded, failed

    # ---------- simple getter  ----------
   #def list_subs(self):
   #    # Build from the single pass index — avoids double traversal
   #    src_index = self.index_subject_sources()
   #    return list(src_index.keys())
