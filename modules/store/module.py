import json
import os
import shutil
from pathlib import Path
import requests

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QTabWidget, QScrollArea, QGridLayout, QLineEdit, QMessageBox,
    QInputDialog, QFrame, QComboBox, QDialog, QListWidget, 
    QListWidgetItem
)

from core.base_module import BaseModule
from core.module_api import get_bus
from core.utils import color
from core.i18n import tr

DEFAULT_REPOS = [
    {"owner": "FreshLend", "repo": "SWARM-Store", "branch": "main"}
]
CONFIG_FILE = Path("config") / "store_repos.json"


def T(key, **kwargs):
    return tr(key, module="store", **kwargs)


class DownloadWorker(QThread):
    finished = Signal(bool, str)

    def __init__(self, files, target_dir, base_path, owner, repo, branch):
        super().__init__()
        self.files = files
        self.target_dir = target_dir
        self.base_path = base_path
        self.owner = owner
        self.repo = repo
        self.branch = branch

    def run(self):
        try:
            for file_info in self.files:
                rel_path = file_info['path'].replace(self.base_path, "").lstrip("/")
                if not rel_path:
                    continue
                    
                dst = os.path.join(self.target_dir, rel_path)
                if file_info['type'] == 'tree':
                    os.makedirs(dst, exist_ok=True)
                elif file_info['type'] == 'blob':
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    raw_url = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{self.branch}/{file_info['path']}"
                    r = requests.get(raw_url, timeout=15)
                    r.raise_for_status()
                    with open(dst, 'wb') as f:
                        f.write(r.content)
            self.finished.emit(True, "Installed successfully.")
        except Exception as e:
            self.finished.emit(False, str(e))


class FetchWorker(QThread):
    finished = Signal(list, str)

    def __init__(self, repos):
        super().__init__()
        self.repos = repos

    def run(self):
        all_items = []
        error = ""
        for r_data in self.repos:
            owner, repo, branch = r_data["owner"], r_data["repo"], r_data["branch"]
            try:
                url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
                r = requests.get(url, timeout=10)
                r.raise_for_status()
                tree = r.json().get("tree", [])
                
                for node in tree:
                    path = node['path']
                    parts = path.split('/')
                    if len(parts) == 3 and parts[0] in ('modules', 'styles') and node['type'] == 'tree':
                        item_type = "Module" if parts[0] == "modules" else "Theme"
                        item_name = parts[2]
                        cat_name = parts[1]
                        
                        meta_path = f"{path}/metadata.json" if item_type == "Module" else f"{path}/theme.json"
                        meta = {}
                        meta_node = next((n for n in tree if n['path'] == meta_path), None)
                        if meta_node:
                            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{meta_path}"
                            try:
                                rm = requests.get(raw_url, timeout=5)
                                if rm.status_code == 200:
                                    meta = rm.json()
                            except Exception:
                                pass
                        
                        all_items.append({
                            "type": item_type,
                            "category": cat_name,
                            "name": meta.get("name", item_name),
                            "folder_name": item_name,
                            "version": meta.get("version", "1.0.0"),
                            "author": meta.get("author", owner),
                            "description": meta.get("description", ""),
                            "path": path,
                            "owner": owner,
                            "repo": repo,
                            "branch": branch
                        })
            except Exception as e:
                error += f"Failed to fetch {owner}/{repo}: {e}\n"
        self.finished.emit(all_items, error)


class ManageReposDialog(QDialog):
    def __init__(self, parent, repos):
        super().__init__(parent)
        self.setWindowTitle(T("dlg_manage_repos_title"))
        self.setMinimumSize(500, 300)
        self.repos = repos
        
        layout = QVBoxLayout(self)
        
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(f"background-color: {color('bg2')}; border: 1px solid {color('border')};")
        layout.addWidget(self.list_widget)
        
        self._refresh()
        
        btns = QHBoxLayout()
        
        reset_btn = QPushButton(T("btn_restore_defaults"))
        reset_btn.clicked.connect(self._restore_defaults)
        btns.addWidget(reset_btn)
        
        btns.addStretch()
        
        close_btn = QPushButton(T("btn_close"))
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        layout.addLayout(btns)

    def _refresh(self):
        self.list_widget.clear()
        for i, r in enumerate(self.repos):
            w = QWidget()
            l = QHBoxLayout(w)
            l.setContentsMargins(8, 4, 8, 4)
            
            is_default = r.get("owner") == "FreshLend" and r.get("repo") == "SWARM-Store"
            label_text = f"{r['owner']}/{r['repo']}"
            if is_default:
                label_text += T("lbl_official")
                
            lbl = QLabel(label_text)
            lbl.setStyleSheet(f"color: {color('fg')};")
            l.addWidget(lbl, 1)
            
            del_btn = QPushButton(T("btn_remove"))
            del_btn.setFixedWidth(80)
            del_btn.setStyleSheet(f"background-color: {color('danger')}; color: white;")
            del_btn.clicked.connect(lambda checked, idx=i: self._remove(idx))
            l.addWidget(del_btn)
            
            item = QListWidgetItem(self.list_widget)
            item.setSizeHint(w.sizeHint())
            self.list_widget.setItemWidget(item, w)

    def _remove(self, idx):
        self.repos.pop(idx)
        self._refresh()

    def _restore_defaults(self):
        self.repos.clear()
        for r in DEFAULT_REPOS:
            self.repos.append(r.copy())
        self._refresh()


class StoreModule(BaseModule):
    name = "Store"
    description = "Browse and install modules and themes."
    version = "1.0.0"
    icon_name = "database"

    def __init__(self):
        self.widget = None
        self.repos = []
        self.remote_items = []
        self.tabs = None
        self.modules_grid = None
        self.themes_grid = None
        self.search_input = None
        self.category_filter = None
        self.author_filter = None
        self.btn_add_repo = None
        self.btn_manage_repos = None
        self.btn_refresh = None
        self.fetch_worker = None
        self.dl_workers = []

    def on_load(self, api):
        self._load_repos()

    def create_widget(self) -> QWidget:
        self.widget = QWidget()
        layout = QVBoxLayout(self.widget)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        top_bar = QHBoxLayout()
        
        self.search_input = QLineEdit()
        self.search_input.textChanged.connect(self._filter)
        top_bar.addWidget(self.search_input, 2)

        self.category_filter = QComboBox()
        self.category_filter.addItem(T("all_categories"), "all")
        self.category_filter.currentIndexChanged.connect(self._filter)
        top_bar.addWidget(self.category_filter, 1)

        self.author_filter = QComboBox()
        self.author_filter.addItem(T("all_authors"), "all")
        self.author_filter.currentIndexChanged.connect(self._filter)
        top_bar.addWidget(self.author_filter, 1)

        self.btn_add_repo = QPushButton()
        self.btn_add_repo.clicked.connect(self._add_repo_dialog)
        top_bar.addWidget(self.btn_add_repo)

        self.btn_manage_repos = QPushButton()
        self.btn_manage_repos.clicked.connect(self._manage_repos_dialog)
        top_bar.addWidget(self.btn_manage_repos)

        self.btn_refresh = QPushButton()
        self.btn_refresh.clicked.connect(self._fetch_data)
        top_bar.addWidget(self.btn_refresh)
        
        layout.addLayout(top_bar)

        self.tabs = QTabWidget()
        
        self.modules_scroll = QScrollArea()
        self.modules_scroll.setWidgetResizable(True)
        self.modules_scroll.setFrameShape(QFrame.NoFrame)
        self.modules_container = QWidget()
        self.modules_grid = QGridLayout(self.modules_container)
        self.modules_grid.setSpacing(16)
        self.modules_grid.setAlignment(Qt.AlignTop)
        self.modules_scroll.setWidget(self.modules_container)
        
        self.themes_scroll = QScrollArea()
        self.themes_scroll.setWidgetResizable(True)
        self.themes_scroll.setFrameShape(QFrame.NoFrame)
        self.themes_container = QWidget()
        self.themes_grid = QGridLayout(self.themes_container)
        self.themes_grid.setSpacing(16)
        self.themes_grid.setAlignment(Qt.AlignTop)
        self.themes_scroll.setWidget(self.themes_container)

        self.tabs.addTab(self.modules_scroll, T("tab_modules"))
        self.tabs.addTab(self.themes_scroll, T("tab_themes"))
        layout.addWidget(self.tabs)

        self.retranslate()
        self._fetch_data()
        return self.widget

    def retranslate(self):
        self.name = T("name", default="Store")
        self.description = T("description", default="Browse and install modules and themes.")
        if not self.widget:
            return

        self.search_input.setPlaceholderText(T("search_placeholder"))
        self.category_filter.setItemText(0, T("all_categories"))
        self.author_filter.setItemText(0, T("all_authors"))
        self.btn_add_repo.setText(T("btn_add_repo"))
        self.btn_manage_repos.setText(T("btn_manage_repos"))
        self.btn_refresh.setText(T("btn_refresh"))
        self.tabs.setTabText(0, T("tab_modules"))
        self.tabs.setTabText(1, T("tab_themes"))
        
        self._render()

    def _load_repos(self):
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    self.repos = json.load(f)
            except Exception:
                self.repos = DEFAULT_REPOS.copy()
        else:
            self.repos = DEFAULT_REPOS.copy()
            self._save_repos()

    def _save_repos(self):
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self.repos, f, indent=2)

    def _add_repo_dialog(self):
        text, ok = QInputDialog.getText(
            self.widget, T("dlg_add_repo_title"), 
            T("dlg_add_repo_text")
        )
        if ok and text.strip():
            parts = text.strip().split("/")
            if len(parts) == 2:
                owner, repo = parts[0].strip(), parts[1].strip()
                if not owner or not repo:
                    QMessageBox.warning(self.widget, T("msg_error_title"), T("msg_invalid_format"))
                    return
                    
                if any(r["owner"] == owner and r["repo"] == repo for r in self.repos):
                    QMessageBox.information(self.widget, T("msg_success_title"), T("msg_repo_exists"))
                    return
                
                self.repos.append({
                    "owner": owner, "repo": repo, "branch": "main"
                })
                self._save_repos()
                self._fetch_data()
            else:
                QMessageBox.warning(self.widget, T("msg_error_title"), T("msg_invalid_format"))

    def _manage_repos_dialog(self):
        dlg = ManageReposDialog(self.widget, self.repos)
        dlg.exec()
        self._save_repos()
        self._fetch_data()

    def _fetch_data(self):
        if self.fetch_worker and self.fetch_worker.isRunning():
            return
        
        self._clear_grids()
        loading = QLabel(T("msg_fetching"))
        loading.setAlignment(Qt.AlignCenter)
        self.modules_grid.addWidget(loading, 0, 0)

        self.fetch_worker = FetchWorker(self.repos)
        self.fetch_worker.finished.connect(self._on_fetched)
        self.fetch_worker.start()

    def _on_fetched(self, items, error):
        self.remote_items = items
        self._update_filters()
        self._render()
        if error:
            print(f"[Store] Errors during fetch: {error}")

    def _update_filters(self):
        self.category_filter.blockSignals(True)
        self.author_filter.blockSignals(True)
        
        cur_cat = self.category_filter.currentData()
        cur_auth = self.author_filter.currentData()
        
        self.category_filter.clear()
        self.category_filter.addItem(T("all_categories"), "all")
        self.author_filter.clear()
        self.author_filter.addItem(T("all_authors"), "all")
        
        cats = sorted(set(item["category"] for item in self.remote_items))
        for c in cats:
            self.category_filter.addItem(c.capitalize(), c)
            
        authors = sorted(set(item["author"] for item in self.remote_items))
        for a in authors:
            self.author_filter.addItem(a, a)
            
        idx_cat = self.category_filter.findData(cur_cat)
        if idx_cat >= 0:
            self.category_filter.setCurrentIndex(idx_cat)
            
        idx_auth = self.author_filter.findData(cur_auth)
        if idx_auth >= 0:
            self.author_filter.setCurrentIndex(idx_auth)
            
        self.category_filter.blockSignals(False)
        self.author_filter.blockSignals(False)

    def _clear_grids(self):
        for grid in (self.modules_grid, self.themes_grid):
            while grid.count():
                item = grid.takeAt(0)
                w = item.widget()
                if w:
                    w.deleteLater()

    def _render(self):
        if not self.modules_grid or not self.themes_grid:
            return
            
        self._clear_grids()
        
        mod_row, theme_row = 0, 0
        for item in self.remote_items:
            card = self._create_card(item)
            if item["type"] == "Module":
                self.modules_grid.addWidget(card, mod_row // 3, mod_row % 3)
                mod_row += 1
            else:
                self.themes_grid.addWidget(card, theme_row // 3, theme_row % 3)
                theme_row += 1

        if mod_row == 0:
            self.modules_grid.addWidget(QLabel(T("msg_no_modules")), 0, 0)
        if theme_row == 0:
            self.themes_grid.addWidget(QLabel(T("msg_no_themes")), 0, 0)

    def _version_tuple(self, v):
        try:
            parts = str(v).split(".")
            return tuple(int(x) for x in parts[:3]) + (0,) * (3 - len(parts))
        except Exception:
            return (0, 0, 0)

    def _create_card(self, item):
        card = QFrame()
        card.setObjectName("card")
        card.setFixedSize(280, 180)
        card.setStyleSheet(f"""
            QFrame#card {{
                background-color: {color('bg3')};
                border: 1px solid {color('border')};
                border-radius: 8px;
            }}
            QFrame#card:hover {{
                border: 1px solid {color('border_hi')};
            }}
        """)
        
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        
        title = QLabel(item["name"])
        title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {color('fg')};")
        title.setWordWrap(True)
        layout.addWidget(title)
        
        meta_text = T("meta_info", version=item['version'], author=item['author'], category=item['category'].capitalize())
        meta = QLabel(meta_text)
        meta.setStyleSheet(f"color: {color('fg_dim')}; font-size: 11px;")
        layout.addWidget(meta)
        
        desc = QLabel(item["description"])
        desc.setStyleSheet(f"color: {color('fg_soft')}; font-size: 12px;")
        desc.setWordWrap(True)
        layout.addWidget(desc, 1)
        
        install_path = Path("modules" if item["type"] == "Module" else "styles") / item["folder_name"]
        is_installed = install_path.exists()
        local_version = "0.0.0"

        if is_installed:
            meta_file = install_path / ("metadata.json" if item["type"] == "Module" else "theme.json")
            if meta_file.exists():
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        local_meta = json.load(f)
                        local_version = str(local_meta.get("version", "0.0.0"))
                except Exception:
                    pass

        btn = QPushButton()
        btn.setFixedHeight(30)

        remote_v = self._version_tuple(item["version"])
        local_v = self._version_tuple(local_version)

        if not is_installed:
            btn.setText(T("btn_install"))
            btn.setStyleSheet(f"background-color: {color('border_hi')}; color: {color('sel_fg')}; font-weight: bold;")
            btn.clicked.connect(lambda: self._install(item, btn))
        elif remote_v > local_v:
            btn.setText(T("btn_update"))
            btn.setStyleSheet(f"background-color: {color('warning')}; color: #000; font-weight: bold;")
            btn.clicked.connect(lambda: self._install(item, btn))
        elif remote_v == local_v:
            btn.setText(T("btn_installed"))
            btn.setEnabled(False)
            btn.setStyleSheet(f"background-color: {color('bg4')}; color: {color('fg_dim')};")
        else:
            btn.setText(T("btn_reinstall"))
            btn.setStyleSheet(f"background-color: {color('bg4')}; color: {color('fg')};")
            btn.clicked.connect(lambda: self._install(item, btn))

        layout.addWidget(btn)
        
        search_text = f"{item['name']} {item['description']}".lower()
        card.setProperty("search_text", search_text)
        card.setProperty("category", item["category"])
        card.setProperty("author", item["author"])
        
        return card

    def _install(self, item, btn):
        btn.setText(T("btn_downloading"))
        btn.setEnabled(False)
        
        api_url = f"https://api.github.com/repos/{item['owner']}/{item['repo']}/git/trees/{item['branch']}?recursive=1"
        try:
            r = requests.get(api_url, timeout=10)
            r.raise_for_status()
            tree = r.json().get("tree", [])
        except Exception as e:
            btn.setText(T("btn_error"))
            btn.setEnabled(True)
            QMessageBox.critical(self.widget, T("msg_error_title"), T("msg_error_tree", error=e))
            return

        files = [node for node in tree if node['path'].startswith(item['path'])]
        target_dir = Path("modules" if item["type"] == "Module" else "styles") / item["folder_name"]
        
        if target_dir.exists():
            shutil.rmtree(target_dir)

        worker = DownloadWorker(
            files, str(target_dir), item['path'], 
            item['owner'], item['repo'], item['branch']
        )
        worker.finished.connect(lambda ok, msg: self._on_install_done(ok, msg, btn))
        self.dl_workers.append(worker)
        worker.start()

    def _on_install_done(self, ok, msg, btn):
        if ok:
            btn.setText(T("btn_installed"))
            btn.setStyleSheet(f"background-color: {color('success')}; color: #000; font-weight: bold;")
            btn.setEnabled(False)
            
            try:
                get_bus().call("core.reload_modules")
                success_text = T("msg_success_auto")
            except Exception as e:
                print(f"[Store] Auto-reload failed: {e}")
                success_text = T("msg_success_text")

            QMessageBox.information(
                self.widget, T("msg_success_title"), success_text
            )
        else:
            btn.setText(T("btn_retry"))
            btn.setEnabled(True)
            QMessageBox.critical(self.widget, T("msg_error_title"), T("msg_error_install", error=msg))

    def _filter(self):
        text = self.search_input.text().strip().lower()
        cat = self.category_filter.currentData()
        auth = self.author_filter.currentData()

        for grid in (self.modules_grid, self.themes_grid):
            for i in range(grid.count()):
                w = grid.itemAt(i).widget()
                if w and isinstance(w, QFrame):
                    search_text = w.property("search_text") or ""
                    item_cat = w.property("category")
                    item_auth = w.property("author")
                    
                    match_text = text in search_text
                    match_cat = (cat == "all") or (item_cat == cat)
                    match_auth = (auth == "all") or (item_auth == auth)
                    
                    if match_text and match_cat and match_auth:
                        w.show()
                    else:
                        w.hide()

    def on_shutdown(self):
        if self.fetch_worker and self.fetch_worker.isRunning():
            self.fetch_worker.terminate()
            self.fetch_worker.wait()
        for w in self.dl_workers:
            if w.isRunning():
                w.terminate()
                w.wait()