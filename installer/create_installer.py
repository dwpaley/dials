"""
Script to set up an installer directory tree and copy over most of the
necessary files.  We used to just keep the entire (Phenix) installer in a
separate SVN tree, but this is inconvenient when we have multiple packages
using the same system and also many third-party dependencies which need to be
kept up to date.  Note that this script provides only the bare minimum
functionality for building CCTBX installers, and other distributions will
probably need to perform additional modifications to the installer tree
before it can be tarred.
"""

import imp
import os
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from optparse import OptionParser

import dials
import libtbx.auto_build.rpath

# XXX HACK
libtbx_path = os.path.abspath(os.path.dirname(libtbx.__file__))
if libtbx_path not in sys.path:
    sys.path.append(libtbx_path)

INSTALL_SH = """\
#!/bin/bash
if [ -z "$PYTHON_EXE" ]; then
  PYTHON_EXE='/usr/bin/python'
  if [ -f "/usr/bin/python2.7" ]; then
    PYTHON_EXE='/usr/bin/python2.7'
  elif [ -f "/usr/bin/python2.6" ]; then
    PYTHON_EXE='/usr/bin/python2.6'
  elif [ -f "/usr/bin/python2" ]; then
    PYTHON_EXE='/usr/bin/python2'
  elif [ -f "./conda_base/bin/python" ]; then
    PYTHON_EXE='./conda_base/bin/python'
  fi
fi
$PYTHON_EXE ./bin/install.py $@
"""

BASHRC = """\
#!/bin/bash
export %(env_prefix)s=$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )
export %(env_prefix)s_VERSION=%(version)s
source $%(env_prefix)s/build/setpaths.sh
"""

CSHRC = """\
#!/bin/csh -f
set testpath=($_)
if ("$testpath" != "") then
    set testpath=$testpath[2]
else
    set testpath=$0
endif
set rootdir=`dirname $testpath`
set fullpath=`cd $rootdir && pwd -P`
setenv LIBTBX_BUILD_RELOCATION_HINT $fullpath
setenv %(env_prefix)s $fullpath
setenv %(env_prefix)s_VERSION %(version)s
source $%(env_prefix)s/build/setpaths.csh
"""


def makedirs(path):
    try:
        os.makedirs(path)
    except Exception:
        print("Directory already exists: %s" % path)


def archive(source, destination, tarfile=None):
    if source == destination:
        print("Source and destination are the same, skipping: %s" % source)
        return
    assert not os.path.exists(destination), "File exists: %s" % destination
    print("Copying: %s -> %s" % (source, destination))
    if not os.path.exists(source):
        print("Warning: source does not exist! Skipping: %s" % source)
        return
    try:
        if (
            os.path.basename(source) != "build"
        ):  # don't delete lib files from python or modules
            shutil.copytree(
                source,
                destination,
                ignore=shutil.ignore_patterns(
                    "*.pyc",
                    "*.pyo",
                    ".svn",
                    ".git",
                    ".swp",
                    ".sconsign",
                    ".o",
                    "*.obj",
                    "*.ilk",
                ),
                symlinks=True,
            )
        else:
            shutil.copytree(
                source,
                destination,
                ignore=shutil.ignore_patterns(
                    "*.lib",
                    "*.pyc",
                    "*.pyo",
                    ".svn",
                    ".git",
                    ".swp",
                    ".sconsign",
                    ".o",
                    "*.obj",
                    "*.ilk",
                ),
                symlinks=True,
            )
    except Exception as err:
        # workaround for possible very long path problem on Windows
        if sys.platform == "win32" and isinstance(err, shutil.Error):
            for e in err[0]:
                if len(e) == 3:  # if not then it's some other error
                    (src, dst, errstr) = e
                    # Prepending \\?\ to path tells Windows to accept paths longer than 260 character
                    if len(src) > 260 or len(dst) > 260:
                        ultdsrc = "\\\\?\\" + src
                        ultddst = "\\\\?\\" + dst
                        shutil.copy(ultdsrc, ultddst)
        else:
            raise Exception(err)


def tar(source, tarfile, cwd=None):
    assert not os.path.exists(tarfile), "File exists: %s" % tarfile
    print("Archiving: %s -> %s" % (source, tarfile))
    # TODO: replace system call to tar with platform independent python tarfile module
    # mytar = tarfile.open(tarfile, mode="r:gz")
    # mytar.add(source, arcname=tarfile)
    # tar.close()
    environment = os.environ.copy()
    subprocess.check_call(["tar", "-cf", tarfile, source], cwd=cwd, env=environment)


class SetupInstaller(object):
    def __init__(self, **kwargs):
        self.install_script = kwargs.get("install_script")
        self.version = kwargs.get("version")
        self.script = kwargs.get("script")
        #
        self.dest_dir = os.path.abspath(kwargs.get("dest_dir"))
        self.root_dir = os.path.abspath(kwargs.get("root_dir"))
        self.dist_dir = os.path.abspath(os.path.join(self.root_dir, "dist"))

        self.license = kwargs.get("license")
        if self.license:
            self.license = os.path.abspath(self.license)
        self.readme = kwargs.get("readme") or [
            os.path.join(libtbx_path, "COPYRIGHT_2_0.txt")
        ]
        if self.readme:
            self.readme = [os.path.abspath(i) for i in self.readme]

        # Is this a source or binary installer?
        self.binary = kwargs.get("binary")

        # Load the installer class, get the list of modules.
        assert os.path.isfile(self.install_script)
        installer_module = imp.load_source("install_script", self.install_script)
        self.installer = installer_module.installer()

        # we only support conda bases
        self.base_dir = "conda_base"

    def run(self):
        # Setup directory structure
        print("Installer will be %s" % self.dest_dir)
        assert not os.path.exists(self.dest_dir), (
            "Installer dir exists: %s" % self.dest_dir
        )
        makedirs(self.dest_dir)
        for i in ["bin", "lib"]:
            makedirs(os.path.join(self.dest_dir, i))
        self.copy_info()
        self.copy_libtbx()
        self.copy_doc()
        self.copy_modules()
        if self.binary:
            self.copy_dependencies()
            self.copy_build()
        self.fix_permissions()
        self.installer.product_specific_prepackage_hook(directory=self.dest_dir)
        self.make_dist()
        if self.binary and sys.platform == "darwin":
            self.make_dist_pkg()
        if self.binary and sys.platform == "win32":
            vcredist = "vcredist_x64.exe"
            shutil.copyfile(
                os.path.join(self.root_dir, "conda_base", vcredist),
                os.path.join(self.dest_dir, vcredist),
            )
            self.make_windows_installer()

    def copy_info(self):
        # Basic setup #
        def copyfile(src, dest):
            # Introduce Windows line breaks when on Windows by read/write in ascii mode
            if sys.platform == "win32":
                open(dest, "w").write(open(src, "r").read())
            else:
                shutil.copyfile(src, dest)

        # Write VERSION
        with open(os.path.join(self.dest_dir, "VERSION"), "w") as f:
            f.write(self.version)
        # Write README
        for i in self.readme:
            copyfile(i, os.path.join(self.dest_dir, os.path.basename(i)))
        # Write LICENSE
        if os.path.isfile(self.license):
            copyfile(self.license, os.path.join(self.dest_dir, "LICENSE"))
        # Actual Python installer script
        shutil.copyfile(
            self.install_script, os.path.join(self.dest_dir, "bin", "install.py")
        )
        if sys.platform != "win32":
            # Write executable Bash script wrapping Python script
            # avoid conflicts with active conda environment during installation
            global INSTALL_SH
            INSTALL_SH = INSTALL_SH.replace(
                "fi\nfi\n$PYTHON_EXE", "fi\nfi\nunset CONDA_PREFIX\n$PYTHON_EXE"
            )
            with open(os.path.join(self.dest_dir, "install"), "w") as f:
                f.write(INSTALL_SH)
            st = os.stat(os.path.join(self.dest_dir, "install"))
            os.chmod(
                os.path.join(self.dest_dir, "install"),
                st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
            )

    def copy_libtbx(self):
        # Copy over libtbx for setup.
        archive(os.path.join(libtbx_path), os.path.join(self.dest_dir, "lib", "libtbx"))

    def copy_dependencies(self):
        # Copy dependencies
        archive(
            os.path.join(self.root_dir, self.base_dir),
            os.path.join(self.dest_dir, self.base_dir),
        )

    def copy_build(self):
        # Copy the entire build directory, minus .o files.
        archive(
            os.path.join(self.root_dir, "build"), os.path.join(self.dest_dir, "build")
        )
        libtbx.auto_build.rpath.run(
            [
                "--otherroot",
                os.path.join(self.root_dir, self.base_dir),
                os.path.join(self.dest_dir, "build"),
            ]
        )

    def copy_modules(self):
        # Source modules #
        for module in set(self.installer.modules):
            archive(
                os.path.join(self.root_dir, "modules", module),
                os.path.join(self.dest_dir, "modules", module),
            )
        s = os.path.join(self.root_dir, "modules", "RevisionNumbers.txt")
        if os.path.exists(s):
            shutil.copyfile(
                s, os.path.join(self.dest_dir, "modules", "RevisionNumbers.txt")
            )

    def copy_doc(self):
        # Copy doc
        archive(os.path.join(self.root_dir, "doc"), os.path.join(self.dest_dir, "doc"))

    def fix_permissions(self):
        if sys.platform == "win32":
            return
        subprocess.check_call(["chmod", "-R", "u+rw,a+rX", self.dest_dir])

    def make_dist(self):
        makedirs(self.dist_dir)
        if sys.platform == "win32":
            # Bake version number into the dispatchers and the help files and create rotarama db
            # This is done during installation on other platforms
            from libtbx.auto_build import installer_utils

            python_exe = os.path.join(self.dest_dir, self.base_dir, "python.exe")
            arg = [
                python_exe,
                os.path.join(self.dest_dir, "bin", "install.py"),
                "--nopycompile",
            ]
            installer_utils.call(arg, cwd=self.dest_dir)

            print("Creating zip archive of distribution")
            fname = os.path.join(
                self.dist_dir, "%s.zip" % os.path.basename(self.dest_dir)
            )
            myzip = zipfile.ZipFile(fname, "w", zipfile.ZIP_DEFLATED, allowZip64=True)
            for dirpath, dirs, files in os.walk(self.dest_dir):
                for f in files:
                    fname = os.path.join(dirpath, f)
                    relfname = os.path.relpath(fname, os.path.join(os.getcwd(), "tmp"))
                    # Prepending \\?\ to path tells Windows to accept paths longer than 260 character
                    if len(fname) >= 260:
                        fname = "\\\\?\\" + fname
                    if len(relfname) >= 260:
                        relfname = "\\\\?\\" + relfname
                    myzip.write(filename=fname, arcname=relfname)
            myzip.close()
        else:
            print("Creating tar archive of distribution")
            tar(
                os.path.basename(self.dest_dir),
                os.path.join(self.dist_dir, "%s.tar" % os.path.basename(self.dest_dir)),
                cwd=os.path.join(self.dest_dir, ".."),
            )

    def make_dist_pkg(self):
        if not os.access("/Applications", os.W_OK | os.X_OK):
            print("Can't access /Applications - skipping .pkg build")
            return

        # This has to match other conventions...
        pkg_prefix = "/Applications"
        app_root_dir = os.path.join(
            pkg_prefix, "%s-%s" % (self.installer.dest_dir_prefix, self.version)
        )
        # app_root_dir = os.path.join(pkg_prefix,
        #                            '%s '% (self.installer.dest_dir_prefix))
        if os.path.exists(app_root_dir):
            shutil.rmtree(app_root_dir)
        subprocess.check_call(
            [os.path.join(self.dest_dir, "install"), "--prefix", pkg_prefix,],
            cwd=self.dest_dir,
        )

        # fix SSL_CERT_FILE location in dispatchers
        # command-line installation step (above) is using the location from the
        # current python, not the one in the newly installed location.
        # command-line installation works correctly outside of this script, though.
        try:
            import certifi
            from libtbx.auto_build.install_base_packages import installer

            file_list = os.listdir(os.path.join(app_root_dir, "build", "bin"))
            for filename in file_list:
                patch = '"$LIBTBX_BUILD/../conda_base/lib/python2.7/site-packages/certifi/cacert.pem"'
                installer.patch_src(
                    os.path.join(app_root_dir, "build", "bin", filename),
                    '"%s"' % certifi.where(),
                    patch,
                )
        except ImportError:
            pass

        tmp = os.path.join(self.root_dir, "tmp")
        makedirs(tmp)
        makedirs(self.dist_dir)
        os.chdir(tmp)  # UGH X 1000.
        from libtbx.auto_build import create_mac_pkg

        create_mac_pkg.run(
            args=[
                "--package_name",
                self.installer.product_name,
                "--organization",
                self.installer.organization,
                "--version",
                self.version,
                "--license",
                self.license,
                "--dist-dir",
                self.dist_dir,
                # "--no_compress",
                app_root_dir,
            ]
        )

    def make_windows_installer(self):
        makedirs(self.dist_dir)
        from libtbx.auto_build import create_windows_installer

        mainscript = os.path.join(
            self.dest_dir, "lib", "libtbx", "auto_build", "mainphenixinstaller.nsi"
        )
        create_windows_installer.run(
            args=[
                "--productname",
                self.installer.product_name,
                "--version",
                self.version,
                "--company",
                self.installer.organization,
                "--website",
                "http://www.phenix-online.org/",
                "--sourcedir",
                os.path.basename(self.dest_dir),
                "--tmpdir",
                os.path.normpath(
                    os.path.join(self.dist_dir, "..", "..", "tmp")
                ),  # location of sourcedir
                "--outdir",
                self.dist_dir,
                "--mainNSISscript",
                mainscript,
            ]
        )


def run(args):
    parser = OptionParser(usage="create_installer.py [options] /destination/directory")
    parser.add_option(
        "--version",
        dest="version",
        action="store",
        help="Package version",
        default=time.strftime("dev%Y%m%d", time.localtime()),
    )
    options, args_ = parser.parse_args(args=args)
    assert len(args_) == 1, "Destination directory required argument."
    setup = SetupInstaller(
        dest_dir=args_[0],
        root_dir=os.path.abspath(os.path.join(dials.__path__[0], "..", "..")),
        version=options.version,
        readme=[os.path.join(dials.__path__[0], "LICENSE")],
        license=os.path.join(dials.__path__[0], "LICENSE"),
        install_script=os.path.join(
            dials.__path__[0], "installer", "dials_installer.py"
        ),
        binary=True,
    )
    setup.run()


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))