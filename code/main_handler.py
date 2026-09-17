import os
import warnings

from jatos_study_ids import (
    handler_ids_from_titles,
    legacy_obs_ids_for_task,
    migration_status,
    refresh_discovered,
)
from data_processing.meta import META_RECREATE
from data_processing.pull_handler import Pull
from data_processing.cc_qc import CCqC
from data_processing.mem_qc import MEM_QC
from data_processing.ps_qc import PS_QC
from data_processing.utils import CONVERT_TO_CSV
from data_processing.wl_qc import WL_QC
from data_processing.plot_utils import CC_PLOTS, MEM_PLOTS, PS_PLOTS
from data_processing.save_utils import SAVE_EVERYTHING
import atexit
from termcolor import cprint

warnings.filterwarnings("ignore")


class Handler:

    def __init__(self):
        self._pbs_titles = refresh_discovered()
        self.IDs = handler_ids_from_titles(self._pbs_titles)
        status = migration_status(self._pbs_titles)
        cprint(
            f"pbsjatos studies: {status['n_present']}/{status['n_expected']} "
            f"(OBS missing: {status['n_missing_obs']})",
            "cyan",
        )
        if status["n_missing_obs"]:
            cprint(
                "OBS OA/OB/OC not fully on pbsjatos — "
                "nightly uses legacy server when JATOS_LEGACY_TOKEN is set",
                "yellow",
            )

        self._meta_recreator = META_RECREATE()
        self._meta_rebuild_pending = False
        atexit.register(self._run_meta_if_needed)
        self._skipped_subjects: list[dict[str, object]] = []

    @staticmethod
    def _normalize_category_value(category):
        """Coerce QC categories to plain scalars so filenames remain clean."""
        if category is None:
            return None
        if hasattr(category, "item"):
            try:
                category = category.item()
            except Exception:
                pass
        try:
            return int(category)
        except (TypeError, ValueError):
            return category

    def _flush_skipped_subjects(self):
        """Log and clear any skipped-subject records accumulated during QC."""
        if self._skipped_subjects:
            formatted = ", ".join(
                f"{entry['subject_id']} (task={entry['task']}, session={entry['session'] if entry['session'] is not None else '<unknown>'}, reason={entry['reason']})"
                for entry in self._skipped_subjects
            )
            cprint(f"Skipped subjects: {formatted}", "red")
            self._skipped_subjects.clear()

    def _run_meta_if_needed(self, force: bool = False):
        """
        Rebuild aggregate CSVs from the saved per-participant artifacts.

        Args:
            force: when True, rebuild even if no dirty flag is set. This
                   is used for the explicit "final step" of the pipeline.
        """
        if not force and not self._meta_rebuild_pending:
            return

        self._flush_skipped_subjects()
        for domain in ("cc", "mem", "ps", "wl"):
            self._meta_recreator.recreate(domain)
        self._meta_rebuild_pending = False

    def pull(self, task):
        token = os.environ.get("JATOS_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "JATOS_TOKEN env var required (see HBC .env/.env). "
                "Do not hardcode tokens in source."
            )
        tease = os.environ.get("TEASE", "")
        proxy = os.environ.get("JATOS_PROXY", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        days_ago = int(os.environ.get("JATOS_DAYS_AGO", "127"))
        txt_dfs = []

        pbs_ids = self.IDs.get(task, [])
        if pbs_ids:
            pull_pbs = Pull(
                pbs_ids,
                tease=tease,
                token=token,
                taskName=task,
                proxy=proxy,
                base_url=os.environ.get("JATOS_BASE_URL"),
            )
            txt_dfs.extend(pull_pbs.load(days_ago=days_ago) or [])

        legacy_pull = os.environ.get("JATOS_LEGACY_PULL", "0").strip().lower() not in (
            "0",
            "false",
            "no",
        )
        legacy_token = os.environ.get("JATOS_LEGACY_TOKEN", "").strip()
        legacy_obs_ids = legacy_obs_ids_for_task(task, set(self._pbs_titles))
        if legacy_pull and legacy_token and legacy_obs_ids:
            legacy_base = os.environ.get(
                "JATOS_LEGACY_BASE_URL", "https://jatos.psychology.uiowa.edu"
            )
            legacy_days = int(os.environ.get("JATOS_LEGACY_DAYS_AGO", "2000"))
            cprint(
                f"legacy OBS pull {task}: studyIds={legacy_obs_ids} "
                f"days_ago={legacy_days}",
                "yellow",
            )
            pull_legacy = Pull(
                legacy_obs_ids,
                tease=tease,
                token=legacy_token,
                taskName=task,
                proxy=proxy,
                base_url=legacy_base,
            )
            txt_dfs.extend(pull_legacy.load(days_ago=legacy_days) or [])

        return self.convert_to_csv(txt_dfs, task)

    def convert_to_csv(self, txt_dfs, task):
        csv_instance = CONVERT_TO_CSV(task)
        csv_dfs = csv_instance.convert_to_csv(txt_dfs)
        result = self.choose_construct(csv_dfs, task)
        self._meta_rebuild_pending = True
        self._run_meta_if_needed(force=True)
        return csv_dfs, result



    def choose_construct(self, csv_dfs, task):

        if task in ['NF', 'AF', 'NTS', 'ATS', 'NNB', 'VNB']:
            return self.qc_cc_dfs(csv_dfs, task)
        elif task in ['FN', 'SM']:
            return self.qc_mem_dfs(csv_dfs, task)
        elif task in ['WL', 'DWL']:
            return self.qc_wl_dfs(csv_dfs, task)
        elif task in ['PC', 'LC', 'DSST']:
            return self.qc_ps_dfs(csv_dfs, task)
        else:
            return None

    def qc_cc_dfs(self, dfs, task):
        categories, plots = [], []
        plot_instance = CC_PLOTS()
        # pick the grouping column for accuracy/RT
        cond_col = "condition" if task in ["AF", "NF", "NNB", "VNB"] else "block_cond"

        # Configure QC with task-specific column names/symbols
        qc_instance = CCqC(
            task,
            MAXRT=1800,
            RT_COLUMN_NAME="response_time",
            ACC_COLUMN_NAME="correct",
            CORRECT_SYMBOL=1,
            INCORRECT_SYMBOL=0,
            COND_COLUMN_NAME=cond_col,
        )


        for df in dfs:
            subject = df["subject_id"].iloc[0]
            if "session" in df.columns:
                session = df["session"].iloc[0]
            elif "session_number" in df.columns:
                session = df["session_number"].iloc[0]
            else:
                session = None

            acc_by: dict = {}
            try:
                # --- Run QC + plots (kept as you had it) ---
                if task in ["AF", "NF"]:
                    category, acc_by = qc_instance.cc_qc(df, threshold=0.5)
                    plot = plot_instance.af_nf_plot(df)
                elif task in ["NNB", "VNB"]:
                    category, acc_by = qc_instance.cc_qc(df, threshold=0.5)
                    plot = plot_instance.nnb_vnb_plot(df)
                else:
                    category, acc_by = qc_instance.cc_qc(df, threshold=0.5, TS=True)
                    plot = plot_instance.ats_nts_plot(df)
            except ValueError as err:
                message = str(err)
                if "No 'test' block rows available for plotting" in message:
                    cprint(
                        f"Skipping subject {subject} for task {task}: {message}",
                        "yellow",
                    )
                    self._skipped_subjects.append(
                        {
                            "task": task,
                            "subject_id": subject,
                            "session": session,
                            "reason": message,
                        }
                    )
                    continue
                raise

            normalized_category = self._normalize_category_value(category)
            categories.append([subject, normalized_category, df])
            plots.append([subject, plot])

        # save artifacts (unchanged)
        save_instance = SAVE_EVERYTHING()
        save_instance.save_dfs(categories=categories, task=task)
        save_instance.save_plots(plots=plots, task=task)

        return categories, plots


    def qc_ps_dfs(self, dfs, task):
        categories, plots = [], []
        plot_instance = PS_PLOTS()
        if task in ['PC', 'LC']:
            ps_instance = PS_QC('response_time', 'correct', 1, 0, 'block_c', 30000)
            for df in dfs:
                subject = df['subject_id'][1]
                category, _ = ps_instance.ps_qc(df, threshold=0.6,)
                if task == 'PC':
                    plot = plot_instance.lc_plot(df)
                elif task == 'LC':
                    plot = plot_instance.lc_plot(df)
                normalized_category = self._normalize_category_value(category)
                categories.append([subject, normalized_category, df])
                plots.append([subject, plot])

        else:
            ps_instance = PS_QC('block_dur', 'correct', 1, 0, 'block_c', 125)
            for df in dfs:
                subject = df['subject_id'][1]
                category, _ = ps_instance.ps_qc(df, threshold=0.6, DSST=True)
                plot = plot_instance.dsst_plot(df)
                normalized_category = self._normalize_category_value(category)
                categories.append([subject, normalized_category, df])
                plots.append([subject, plot])

        save_instance = SAVE_EVERYTHING()
        save_instance.save_dfs(categories=categories, task=task)
        save_instance.save_plots(plots=plots, task=task)

        return categories, plots


    def qc_mem_dfs(self, dfs, task):
        plot_instance = MEM_PLOTS()
        categories, plots = [], []
        if task in ['FN']:
            mem_instance = MEM_QC('response_time', 'correct', 1, 0, 'block_c', 4000)
            for df in dfs:
                subject_series = df.get('subject_id')
                subject = (
                    subject_series.iloc[1]
                    if subject_series is not None and len(subject_series) > 1
                    else (subject_series.iloc[0] if subject_series is not None and not subject_series.empty else "<unknown>")
                )
                if 'session_number' in df.columns and len(df['session_number']) > 1:
                    session = df['session_number'].iloc[1]
                elif 'session' in df.columns and len(df['session']) > 1:
                    session = df['session'].iloc[1]
                elif 'session_number' in df.columns and not df['session_number'].empty:
                    session = df['session_number'].iloc[0]
                elif 'session' in df.columns and not df['session'].empty:
                    session = df['session'].iloc[0]
                else:
                    session = None

                try:
                    category, _ = mem_instance.fn_sm_qc(df, threshold=0.5)
                    plot = plot_instance.fn_plot(df)
                except ValueError as err:
                    message = str(err)
                    if "No 'test' block rows available for MEM plotting" in message:
                        cprint(
                            f"Skipping subject {subject} for task {task}: {message}",
                            "yellow",
                        )
                        self._skipped_subjects.append(
                            {
                                "task": task,
                                "subject_id": subject,
                                "session": session,
                                "reason": message,
                            }
                        )
                        continue
                    raise
                normalized_category = self._normalize_category_value(category)
                categories.append([subject, normalized_category, df])
                plots.append([subject, plot])
        elif task in ['SM']:
            mem_instance = MEM_QC('response_time', 'correct', 1, 0, 'block_c', 2000)
            for df in dfs:
                subject_series = df.get('subject_id')
                subject = (
                    subject_series.iloc[1]
                    if subject_series is not None and len(subject_series) > 1
                    else (subject_series.iloc[0] if subject_series is not None and not subject_series.empty else "<unknown>")
                )
                if 'session_number' in df.columns and len(df['session_number']) > 1:
                    session = df['session_number'].iloc[1]
                elif 'session' in df.columns and len(df['session']) > 1:
                    session = df['session'].iloc[1]
                elif 'session_number' in df.columns and not df['session_number'].empty:
                    session = df['session_number'].iloc[0]
                elif 'session' in df.columns and not df['session'].empty:
                    session = df['session'].iloc[0]
                else:
                    session = None

                try:
                    category, _ = mem_instance.fn_sm_qc(df, threshold=0.5)
                    plot = plot_instance.sm_plot(df)
                except ValueError as err:
                    message = str(err)
                    if "No 'test' block rows available for MEM plotting" in message:
                        cprint(
                            f"Skipping subject {subject} for task {task}: {message}",
                            "yellow",
                        )
                        self._skipped_subjects.append(
                            {
                                "task": task,
                                "subject_id": subject,
                                "session": session,
                                "reason": message,
                            }
                        )
                        continue
                    raise
                normalized_category = self._normalize_category_value(category)
                categories.append([subject, normalized_category, df])
                plots.append([subject, plot])
        save_instance = SAVE_EVERYTHING()
        save_instance.save_dfs(categories=categories, task=task)
        save_instance.save_plots(plots=plots, task=task)

        return categories, plots


    def qc_wl_dfs(self, dfs, task):
        categories, plots = [], []
        plot_instance = MEM_PLOTS()

        if task == 'WL':
            for df in dfs:
                subject = df['subject_id'].iloc[1]
                version = df['task_vers'].iloc[1]
                session = (df['session_number'].iloc[1] if 'session_number' in df.columns
                           else (df['session'].iloc[1] if 'session' in df.columns else None))

                wl_instance = WL_QC()
                df_all, category = wl_instance.wl_qc(df, version)
                plot = plot_instance.wl_plot(df_all)

                normalized_category = self._normalize_category_value(category)
                categories.append([subject, normalized_category, df])
                plots.append([subject, plot])

        elif task == 'DWL':
            for df in dfs:
                subject = df['subject_id'].iloc[1]
                version = df['task_vers'].iloc[1]
                session = (df['session_number'].iloc[1] if 'session_number' in df.columns
                           else (df['session'].iloc[1] if 'session' in df.columns else None))

                dwl_instance = WL_QC()
                df_all, category = dwl_instance.dwl_qc(df, version)
                plot = plot_instance.dwl_plot(df_all)

                normalized_category = self._normalize_category_value(category)
                categories.append([subject, normalized_category, df])
                plots.append([subject, plot])

        # maybe: materialize wl_master back to columns if you prefer
        # wl_master_out = self.wl_master.reset_index()

        save_instance = SAVE_EVERYTHING()
        save_instance.save_dfs(categories=categories, task=task)
        save_instance.save_plots(plots=plots, task=task)

        return categories, plots
if __name__ == '__main__':
    import os
    import sys

    task_list = ['AF', 'NF', 'NTS', 'ATS', 'NNB', 'VNB', 'WL', 'DWL', 'FN', 'SM', 'PC', 'LC', 'DSST']
    if sys.argv[1] == 'all':
        instance = Handler()
        for task in task_list:
            csv_dfs = instance.pull(task=task)
    elif sys.argv[1] in task_list:
        instance = Handler()
        csv_dfs = instance.pull(task=sys.argv[1])
