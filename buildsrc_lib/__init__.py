import filecmp
import os
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from datetime import date
from os.path import abspath, exists, isfile, islink, join

import requests
from debian.deb822 import Deb822
from packaging.version import InvalidVersion, Version

CWD = abspath(os.getcwd())
TMP = join(CWD, "tmp")
DEBIAN_DIR = join(CWD, "debian")
MODULES = join(CWD, "modules")
THEMES = join(CWD, "themes")
WEBMIN_CORE = join(CWD, "webmin_core")
MODULE_WBM = join(CWD, "module-archives")
THEME_WBM = join(CWD, "theme-archives")
CTRL_FILE = join(CWD, "debian/control")
SIGNING_KEY = join(CWD, "jcameron-key.asc")
KEYRING = join(TMP, "webmin.gpg")


source_control = Deb822("""
Source: webmin
Section: admin
Priority: optional
Maintainer: Jeremy Davis <jeremy@turnkeylinux.org>
Build-Depends: debhelper (>= 10), gzip, tar
Standards-Version: 4.0.0
Homepage: https://webmin.com/
Vcs-Browser: https://github.com/turnkeylinux/webmin/
Vcs-Git: https://github.com/turnkeylinux/webmin.git
""")

webmin_core_control = Deb822("""
Package: webmin
Architecture: all
Depends:
 libauthen-pam-perl,
 libio-pty-perl,
 libnet-ssleay-perl,
 libpam-runtime,
 openssl,
 perl,
 ${misc:Depends},
Pre-Depends: perl
Description: A web-based administration interface for Unix systems.
 Using Webmin you can configure DNS, Samba, NFS, local/remote filesystems
 and more using your web browser. After installation, enter the URL
 https://localhost:10000/ into your browser and login as root with your
 root password.
""")


class WebminUpdateError(Exception):
    pass


def get_remote_versions(
    user_repo: str, stable_only: bool = True, quiet: bool = True
) -> list[str]:
    """Leverages 'gh_releases' to return a list of validated versions in order
    from newest to oldest - with or without pre-release versions
    """

    # find 'gh_releases' if not in PATH and common available
    def common(path: str) -> str:
        return join(
            path,
            "overlays/turnkey.d/github-latest-release",
            "usr/local/bin/gh_releases",
        )

    gh_releases = "gh_releases"
    for gh_bin in (
        shutil.which("gh_releases"),
        *list(map(common, ("/turnkey/fab/common", "/turnkey/public/common"))),
    ):
        if gh_bin and exists(gh_bin):
            gh_releases = gh_bin
            break
    if not quiet:
        print("Checking for new upstream version - please wait...")
    try:
        version_proc = subprocess.run(
            [gh_releases, user_repo],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        raise WebminUpdateError(e) from e
    # use a dict to preserve original version strings while also sorting,
    # validating and removing pre-releases versions where relevant
    _versions: dict[Version, str] = {}
    for version in version_proc.stdout.strip().split("\n"):
        if version:
            try:
                _version = Version(version)
                if not _version.is_prerelease:
                    _versions[_version] = version
                elif not stable_only:
                    _versions[_version] = version
            except InvalidVersion as e:
                raise WebminUpdateError(e) from e
    sorted_versions = dict(sorted(_versions.items(), reverse=True))
    if sorted_versions:
        return list(sorted_versions.values())
    raise WebminUpdateError("Remote Webmin version not found")


def get_local_version(path: str = WEBMIN_CORE, force: bool = False) -> str:
    """Read version from '<path>/webmin_core'."""
    version_path = join(path, "version")
    try:
        with open(version_path) as fob:
            version = fob.read().strip()
    except OSError as e:
        if not force:
            raise WebminUpdateError(e) from e
        version = "0"
    if version:
        return version
    raise WebminUpdateError(f"No version found (looked in {version_path})")


def download(out_path: str, url: str) -> None:
    response = requests.get(url)
    if not response.ok:
        raise WebminUpdateError(f"Failed to download url: {url}")
    with open(join(out_path), "wb") as fob:
        fob.write(response.content)


def untar(outdir: str, tarball: str, force: bool = False) -> None:
    if exists(outdir):
        if force:
            os.remove(outdir)
        else:
            raise WebminUpdateError(
                f"{outdir} exists; use -f|--force to overwrite"
            )
    os.makedirs(outdir)
    with tarfile.open(tarball, "r:gz") as tar:
        tar.extractall(path=outdir)


def trim_line(line: str, line_length: int = 60) -> list[str]:
    """Trim lines to max 60 chars and returns lines as a list."""
    lines_to_return: list[str] = []
    start_index: int = 0
    last_safe_split: int = 0
    i: int = 0
    line = line.replace("\n", " ")
    while True:
        if len(line) <= i:
            return [*lines_to_return, line[start_index:].strip()]
        if line[i] == " ":
            last_safe_split = i
        if i - start_index >= line_length:
            lines_to_return.append(line[start_index:last_safe_split].strip())
            start_index = last_safe_split
            i = start_index
        else:
            i += 1


class _Common:
    def _p(self, msg: str, quiet: bool = False, error: bool = False) -> None:
        if error:
            # echo error messages even when quiet=True
            print(msg, file=sys.stderr)
        if not quiet:
            print(msg)


@dataclass
class Plugin(_Common):
    """Object representing a module or theme"""

    name: str
    source_dir: str
    version: str
    # only depend on installable modules
    installable_mods: list[str]
    strict: bool = True
    quiet: bool = False

    def __post_init__(self) -> None:
        self.dir = join(self.source_dir, self.name)
        self.type = self._plugin_type()
        self.info = self._read_info()

        # if "plugin" is a link, then:
        #   - take note of the target
        #   - add link target to info["depends"] property
        #   - generate an empty dir with a readme when running the move method
        self.link = ""
        if islink(self.dir):
            self.link = os.readlink(self.dir)
            self.info["depends"] = f"{self.info['depends']} {self.link}"

    def _plugin_type(self) -> str:
        for _type in ["module", "theme"]:
            info_file = join(self.dir, f"{_type}.info")
            if exists(info_file):
                return _type
        if self.strict:
            raise WebminUpdateError(
                f"Module/theme info file not found: {self.dir}"
            )
        return ""

    def _fix_deps(self, plugin: str, deps: str) -> str:
        """Fix dependencies for the control "Depends" field as needed."""
        if plugin == "fdisk":
            self._update_quilt_patch()
            return deps.replace("raid", "")
        elif plugin == "lvm":
            self._update_quilt_patch()
            if "raid" not in deps:
                return f"{deps} raid"
        return deps

    def _get_old_version(self) -> str:
        """Run git diff on webmin_core/version to get old version."""
        git_diff = subprocess.run(
            ["/usr/bin/git", "diff", "--", join(WEBMIN_CORE, "version")],
            capture_output=True,
            text=True,
        )
        print(f"{git_diff.stdout=}")
        for line in git_diff.stdout.split("\n"):
            print(f"git diff line: {line}")
            if line and line[0] == "-" and line[1].isdigit():
                return line[1:].strip()
        raise WebminUpdateError("Old version not found using git diff")

    def _update_quilt_patch(self) -> None:
        """Update Webmin version in Debian module.info quilt patch."""
        print("*** updating quilt patch ***")
        old_version = get_local_version(WEBMIN_CORE)
        changed_lines = ["-", "+"]
        patch_file = join(
            DEBIAN_DIR, "patches", "fix-module-dependencies.diff"
        )
        # module path to find in the quilt patch
        info_path = join("modules", self.name, "module.info")
        replace_next = False
        old_version = self._get_old_version()
        try:
            with open(patch_file) as fob:
                lines = fob.readlines()
            with open(patch_file, "w") as fob:
                for line in lines:
                    print(f"*- orig line:\t{line}")
                    if line.startswith("Last-Update:"):
                        print("** line starts with 'Last-Update:'")
                        today = date.today().strftime("%Y-%m-%d")
                        line = f"Last-Update: {today}\n"
                    elif line.startswith("+++") and info_path in line:
                        print("** found start line'")
                        replace_next = True
                    elif (
                        replace_next
                        and line[0] in changed_lines
                        and "depends=" in line
                    ):
                        print(f"** depends line... - replacing '{old_version}'"
                              f" with '{self.version}'")
                        line = line.replace(old_version, self.version)
                        replace_next = False
                    print(f"*+ new line :\t{line}")
                    fob.write(line)
        except OSError as e:
            raise WebminUpdateError(e) from e


    def _read_info(self) -> dict[str, str]:
        """Read the relevant info from the plugin '.info' file"""
        info_file = join(self.dir, f"{self.type}.info")
        info = {"os_support": "", "depends": "", "desc": "", "longdesc": ""}
        with open(info_file) as fob:
            for line in fob:
                # assuming all lines are key=value
                item, content = line.split("=", 1)
                if item in info.keys():
                    info[item] = content.strip()
        return info

    @property
    def debian_support(self) -> bool:
        """Check if plugin supports Debian."""
        os_support = self.info["os_support"]
        if (
            os_support == "!windows"
            or "debian-linux" in os_support
            or "*-linux" in os_support
            or self.type == "theme"  # themes are always OS agnostic
        ):
            return True
        return False

    @property
    def control(self) -> Deb822:
        """Generate plugin control fields."""
        self._p(f"- generating control for {self.type}: {self.name}")
        depends = self.info["depends"]
        # patch required dependencies
        depends = self._fix_deps(self.name, self.info["depends"])
        ctrl_depends = [f"webmin (>= {self.version})"]
        for depend in depends.split(" "):
            if not depend or depend[0].isdigit():
                continue
            # only depend on installable modules
            if depend in self.installable_mods:
                ctrl_depends.append(f"webmin-{depend}")
        joined_depends = ", ".join(ctrl_depends)
        if len(f"Depends: {joined_depends}") > 60:
            joined_depends = "\n " + ",\n ".join(ctrl_depends)
        description = "\n ".join(
            [
                f"Webmin {self.type} - {self.info['desc']}",
                *trim_line(self.info["longdesc"]),
            ]
        )
        return Deb822(
            {
                "Package": f"webmin-{self.name}",
                "Architecture": "all",
                "Depends": joined_depends,
                "Description": description,
            }
        )

    def move(self, base_dst_dir: str = "") -> None:
        if not base_dst_dir:
            base_dst_dir = join(CWD, f"{self.type}s")
        dst_dir = join(base_dst_dir, self.name)
        if dst_dir == self.dir:
            self._p("Nothing to do")
            return
        os.makedirs(base_dst_dir, exist_ok=True)
        if self.link:
            # see __post_init__ for info
            os.makedirs(dst_dir)
            with open(join(dst_dir, "README"), "w") as fob:
                fob.write(
                    "README\n======\n"
                    f"\nThe original {self.name} {self.type} was a symlink"
                    f" to {self.link} {self.type}"
                    "\nThis directory is intentionally left empty and the"
                    f" generated package will depend on the target {self.type}"
                    "\n"
                )
        else:
            os.rename(self.dir, dst_dir)
        self.dir = dst_dir


class Webmin(_Common):
    """Webmin source object with methods for updating and processing."""
    def __init__(
        self,
        force: bool = False,
        quiet: bool = False,
    ) -> None:
        self.force = force
        self.quiet = quiet
        self.module_no = self._count(MODULES)
        self.theme_no = self._count(THEMES)
        self.local_version = get_local_version(WEBMIN_CORE, force=self.force)
        self.stable_only = True
        self.remote_versions: list[str] = []


    def get_remote_version(
        self,
        version: str = "latest",
        stable_only: bool = True,
        force_update: bool = False,
    ) -> str:
        """Return version number of upstream Webmin source; either:

        - latest version (version = "latest")
        - requested version - if it exists (format: x.xxx)
        - note cached info will be used unless either no cached data exists or
          <force_update>
        Raises exception if no matching version found.
        """

        if not self.remote_versions or force_update:
            self._p("Checking for new upstream versions - please wait...")
            self.remote_versions = get_remote_versions(
                "webmin/webmin", stable_only=stable_only
            )
        if version == "latest":
            return self.remote_versions[0]
        elif version in self.remote_versions:
            return version
        raise WebminUpdateError(
            f"version '{version}' not found or not valid"
            f" - available versions: {self.remote_versions}"
        )

    @property
    def latest_version(self) -> str:
        """Show latest stable upstream version"""
        return self.get_remote_version()

    def new_version(self, check_only: bool = False) -> str:
        """Return version string of new version if available."""
        local_v = self.local_version
        remote_v = self.latest_version
        msg = f"- local version: {local_v}, remote version: {remote_v}"
        if Version(local_v) < Version(remote_v):
            self._p(f"New version available {msg}")
            if check_only:
                sys.exit(0)
            self._new_ver = remote_v
            return remote_v
        elif local_v == remote_v:
            self._p(f"No update available {msg}")
            if check_only:
                sys.exit(100)
            if self.force:
                return remote_v
            return ""
        # local version newer than remote - shouldn't ever happen
        raise WebminUpdateError(
            f"local Webmin version ({local_v})"
            f" is newer than remote ({remote_v})"
        )

    def _count(self, path: str) -> int:
        if exists(path):
            return len(os.listdir(path))
        return 0

    def _clean_paths(self) -> None:
        self._p("Cleaning paths")
        for path in [TMP, MODULES, THEMES, WEBMIN_CORE]:
            self._p(f"- {path}")
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                pass
            os.makedirs(path)

    def valid_version(
        self,
        webmin_all_path: str,
        webmin_min_path: str = WEBMIN_CORE,
        version: str = "",
    ) -> bool:
        """Validate all version numbers match."""
        all_version = get_local_version(webmin_all_path)
        min_version = get_local_version(webmin_min_path)
        valid = all_version == min_version
        if version:
            valid = valid and version == all_version
        if not valid:
            raise WebminUpdateError("version validation failed")
        return True

    def load_plugins(
        self,
        webmin_all_path: str,
        webmin_min_path: str = WEBMIN_CORE,
        version: str = "",
        skip_validation: bool = False,
    ) -> None:
        """Load modules and themes from the webmin_all_path directory.

        - webmin_all_path and webmin_min_path are path to contents of
          (unpacked) webmin-x.xxx.tar.gz and webmin-x.xxx-minimal.tar.gz
          directories respectively
        - if version not set will be read from webmin_all_path
        - determines plugins by comparing contents of webmin_all_path &
          webmin_min_path (any dir not in webmin_min_path is assumed to be a
          plugin)
        - plugins not supported on Debian are skipped
        - matching plugin directories are moved to plugin_type/plugin_name
        """
        self._p("Processing modules and themes")
        if not skip_validation:
            self.valid_version(webmin_all_path, webmin_min_path, version)
        self.modules = []
        self.themes = []
        compare_dirs = filecmp.dircmp(webmin_min_path, webmin_all_path)
        core_only = compare_dirs.left_only
        full_only = compare_dirs.right_only
        if core_only != ["minimal-install"]:
            if "minimal-install" in core_only:
                core_only.remove("minimal-install")
            core_only_objs = ", ".join(core_only)
            raise WebminUpdateError(
                f"unexpected objects in {WEBMIN_CORE}: {core_only_objs}"
            )
        for item in full_only:
            item_path = join(webmin_all_path, item)
            if isfile(item_path):
                raise WebminUpdateError(f"Unexpected file: {item_path}")
            self._p(f"- processing item: {item}")
            plugin = Plugin(
                name=item,
                source_dir=webmin_all_path,
                version=version,
                installable_mods=full_only,
                quiet=self.quiet,
            )
            if not plugin.debian_support:
                self._p(f"{item} not supported on Debian - skipping")
                continue
            self._p(f"- moving {plugin.type}: {item}")
            plugin.move()
            if plugin.type == "module":
                self.modules.append(plugin)
            else:
                self.themes.append(plugin)

    def _validate_file(self, file: str, sig: str) -> None:
        """Validate file signatures."""
        gpg_cmd = ["gpg", "--no-default-keyring", "--keyring", KEYRING]
        if not exists(KEYRING):
            self._p(f"- generating temp keyring: {KEYRING}")
            try:
                subprocess.run(
                    [*gpg_cmd, "--import", SIGNING_KEY],
                    capture_output=True,
                    text=True,
                    check=True,
                )
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                raise WebminUpdateError(e) from e
        validate = subprocess.run(
            [*gpg_cmd, "--verify", sig, file],
            capture_output=True,
            text=True,
        )
        if validate.returncode == 0:
            self._p(f"- validated file: {file}")
        else:
            raise WebminUpdateError(validate.stderr)

    def download(
        self,
        version: str = "",  # default to latest stable
        force: bool | None = None,
    ) -> None:
        """Download, validate and unpack webmin-<version>[-minimal].tar.gz
        files.
        """
        if force is None:
            force = self.force
        if not exists(join(CWD, ".git")) and not self.force:
            raise WebminUpdateError(
                "No '.git' dir found - not downloading new files without force"
            )
        if not version:
            version = self.latest_version
        else:
            version = self.get_remote_version(version)
        self._p(f"Downloading and validating files for version: {version}")
        gh_url = "https://github.com/webmin/webmin/releases/download"
        base_url = join(gh_url, version)
        sig_url = "https://download.webmin.com/download/sigs"
        for name in [f"webmin-{version}", f"webmin-{version}-minimal"]:
            tarball = f"{name}.tar.gz"
            sig = f"{tarball}-sig.asc"
            for file, file_url in (
                (tarball, join(base_url, tarball)),
                (sig, join(sig_url, sig)),
            ):
                file_path = join(TMP, file)
                self._p(f"- downloading {file} ({file_url})")
                download(file_path, file_url)
            self._p(f"- validating {tarball} (signature file: {sig})")
            self._validate_file(join(TMP, tarball), join(TMP, sig))
            self._p(f"- unpacking {tarball} to {join(TMP, name)}")

            if name.endswith("minimal"):
                tmp = join(TMP, "core")
            else:
                tmp = join(TMP, "all")
            untar(tmp, join(TMP, tarball))

    def update(self, version: str = "", force: bool | None = None) -> bool:
        """Update Webmin source if 'version' > local version - or force=True.

        Default version is latest upstream stable. Version downgrades are
        supported.
        """

        if force is None:
            force = self.force
        if not version or version == "latest":
            version = self.latest_version
        else:
            version = self.get_remote_version(version)
        if version == self.local_version:
            if force:
                self._p(f"Forcing rebuild of version {version}")
            else:
                self._p(f"Nothing to do - local version already {version}")
                return False
        self._clean_paths()
        self.download(version, force)
        webm_core_tmp = join(TMP, "core", f"webmin-{version}")
        self._p(f"Moving {webm_core_tmp} to {WEBMIN_CORE}")
        os.rename(join(TMP, "core", f"webmin-{version}"), WEBMIN_CORE)
        self.load_plugins(
            join(TMP, "all", f"webmin-{version}"), version=version
        )
        self._p(f"Updated Webmin source to {version}")
        self._p(f"- {len(self.modules)} modules and {len(self.themes)} themes")
        return True

    def dump_control(self) -> str:
        """Generate control file contents."""
        full_control = [source_control.dump(), webmin_core_control.dump()]
        for plugin in sorted(
            [*self.modules, *self.themes], key=lambda x: x.name
        ):
            full_control.append(plugin.control.dump())
        return "\n".join(full_control)

    def write_control(self, control_file: str = CTRL_FILE) -> None:
        """Write control file."""
        self._p("Writing control file")
        with open(control_file, "w") as fob:
            fob.write(self.dump_control())
