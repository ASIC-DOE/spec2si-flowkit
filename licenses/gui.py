#!/usr/bin/env python3
"""Tkinter GUI for the FlexLM license checker (spec2si-flowkit/licenses).

Shows tool families collapsed to seat counts by default; click a feature
row to see who holds its seats and for how long -- that detail only ever
renders on request, never in the default view. "Cluster..." opens a dialog
to point the tool at a cluster other than BNL and persist the change.

  python3 -m licenses.gui
"""
import threading
import tkinter as tk
from tkinter import messagebox, ttk

try:
    from . import lm
except ImportError:  # allow `python3 gui.py` as well as `-m licenses.gui`
    import lm


class ClusterDialog(tk.Toplevel):
    """Edit host / license-server / lmstat / server-port map, then Save
    persists it via lm.save_settings() and Apply hands it back without
    saving."""

    def __init__(self, master, settings, on_apply):
        super().__init__(master)
        self.title("Cluster settings")
        self.resizable(False, False)
        self._on_apply = on_apply
        self._entries = {}

        form = ttk.Frame(self, padding=10)
        form.pack(fill="both", expand=True)

        def row(r, key, label, width=40):
            ttk.Label(form, text=label).grid(row=r, column=0, sticky="w", pady=2)
            e = ttk.Entry(form, width=width)
            e.insert(0, str(settings.get(key, "")))
            e.grid(row=r, column=1, sticky="w", pady=2)
            self._entries[key] = e

        row(0, "host", "SSH host:")
        row(1, "lic_server_host", "License server host:")
        row(2, "lmstat", "lmstat path:")
        row(3, "lmstat_glob", "lmstat glob (fallback):")

        ttk.Label(form, text="Servers (one PORT:LABEL per line):").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(8, 2))
        self._servers_text = tk.Text(form, width=48, height=8)
        self._servers_text.insert(
            "1.0", "\n".join("%d:%s" % (p, l) for p, l in settings["servers"]))
        self._servers_text.grid(row=5, column=0, columnspan=2, sticky="w")

        btns = ttk.Frame(form)
        btns.grid(row=6, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="Apply", command=self._apply).pack(side="right", padx=4)
        ttk.Button(btns, text="Save + Apply", command=self._save).pack(side="right", padx=4)

    def _read(self):
        settings = {k: e.get().strip() for k, e in self._entries.items()}
        servers = []
        for line in self._servers_text.get("1.0", "end").splitlines():
            line = line.strip()
            if not line:
                continue
            port_s, _, label = line.partition(":")
            try:
                port = int(port_s)
            except ValueError:
                messagebox.showerror("Cluster settings", "bad server line: %r" % line)
                return None
            servers.append([port, label or ("port %d" % port)])
        if not servers:
            messagebox.showerror("Cluster settings", "at least one server is required")
            return None
        settings["servers"] = servers
        try:
            lm._validate(settings)
        except lm.SettingsError as exc:
            messagebox.showerror("Cluster settings", str(exc))
            return None
        return settings

    def _apply(self):
        settings = self._read()
        if settings is None:
            return
        self._on_apply(settings, save=False)
        self.destroy()

    def _save(self):
        settings = self._read()
        if settings is None:
            return
        self._on_apply(settings, save=True)
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("License Checker")
        self.geometry("780x520")
        self.settings = lm.load_settings()
        self.snapshot = None
        self._feature_index = {}

        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill="x")
        self.status = ttk.Label(top, text="not fetched yet")
        self.status.pack(side="left")
        ttk.Button(top, text="Cluster...", command=self._open_cluster_dialog).pack(side="right", padx=(4, 0))
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="right")

        pan = ttk.PanedWindow(self, orient="vertical")
        pan.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        tree_frame = ttk.Frame(pan)
        self.tree = ttk.Treeview(tree_frame, columns=("stat",), show="tree headings")
        self.tree.heading("#0", text="Tool family / feature")
        self.tree.heading("stat", text="in use / issued (free)")
        self.tree.column("stat", width=220, anchor="e")
        self.tree.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        pan.add(tree_frame, weight=2)

        detail_frame = ttk.Frame(pan)
        self.detail_label = ttk.Label(detail_frame, text="Holders (click a feature above)")
        self.detail_label.pack(anchor="w")
        cols = ("user", "host", "start", "held")
        self.detail = ttk.Treeview(detail_frame, columns=cols, show="headings", height=7)
        for c, w in (("user", 100), ("host", 220), ("start", 140), ("held", 100)):
            self.detail.heading(c, text=c.capitalize())
            self.detail.column(c, width=w)
        self.detail.pack(fill="both", expand=True)
        pan.add(detail_frame, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        self.refresh()

    def _open_cluster_dialog(self):
        ClusterDialog(self, self.settings, self._on_settings_applied)

    def _on_settings_applied(self, settings, save):
        self.settings = settings
        if save:
            lm.save_settings(settings)
        self.refresh()

    def refresh(self):
        self.status.configure(text="querying %s..." % self.settings.get("host"))
        self.tree.delete(*self.tree.get_children())
        self.detail.delete(*self.detail.get_children())
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        snap = lm.collect(settings=self.settings)
        self.after(0, self._populate, snap)

    def _populate(self, snap):
        self.snapshot = snap
        if not snap["fetched_ok"]:
            self.status.configure(text="unreachable (%s): %s" % (snap["host"], snap["error"]))
            return
        self.status.configure(text="fetched from %s" % snap["host"])
        self._feature_index.clear()
        for family, info in snap["families"].items():
            label = family if info["reachable"] else family + "  [UNREACHABLE]"
            fid = self.tree.insert("", "end", text=label, values=("",), open=True)
            for name, f in sorted(info.get("features", {}).items()):
                stat = "%d/%d (%d free)" % (f["in_use"], f["issued"], f["free"])
                iid = self.tree.insert(fid, "end", text=name, values=(stat,))
                self._feature_index[iid] = (family, name)

    def _on_select(self, _evt):
        sel = self.tree.selection()
        self.detail.delete(*self.detail.get_children())
        if not sel:
            return
        info = self._feature_index.get(sel[0])
        if info is None:
            self.detail_label.configure(text="Holders (click a feature above)")
            return
        family, name = info
        feat = self.snapshot["families"][family]["features"][name]
        self.detail_label.configure(
            text="Holders of %s -- %s (%d/%d in use)" %
            (family, name, feat["in_use"], feat["issued"]))
        for u in feat["users"]:
            held = lm.format_duration(u["start"])
            self.detail.insert("", "end", values=(u["user"], u["host"], u["start"], held))


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
