# -*- coding: utf-8 -*-
# Copyright 2010-2021, Google Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
#     * Redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above
# copyright notice, this list of conditions and the following disclaimer
# in the documentation and/or other materials provided with the
# distribution.
#     * Neither the name of Google Inc. nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""A helper script to build Qt from the source code.

A helper script to build Qt from the source code mainly for masOS and Windows
with dropping unnecessary features to minimize the installer size.

  python build_tools/build_qt.py --debug --release --confirm_license

By default, this script assumes that Qt archive is stored as

  src/third_party_cache/qtbase-everywhere-opensource-src-5.15.10.tar.xz

and Qt src files that are necessary to build Mozc will be checked out into

  src/third_party/qt_src.

This way we can later delete src/third_party/qt_src to free up disk space by 2GB
or so.
"""

import argparse
from collections.abc import Iterator
import dataclasses
import functools
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import time
from typing import Any, Union
import zipfile


ABS_SCRIPT_PATH = pathlib.Path(__file__).absolute()
# src/build_tools/build_qt.py -> src/
ABS_MOZC_SRC_DIR = ABS_SCRIPT_PATH.parents[1]
ABS_QT_SRC_DIR = ABS_MOZC_SRC_DIR.joinpath('third_party', 'qt_src')
ABS_QT_DEST_DIR = ABS_MOZC_SRC_DIR.joinpath('third_party', 'qt')
# The archive filename should be consistent with update_deps.py.
ABS_QT_ARCHIVE_PATH = ABS_MOZC_SRC_DIR.joinpath(
    'third_party_cache', 'qtbase-everywhere-opensource-src-5.15.10.tar.xz')
ABS_JOM_ARCHIVE_PATH = ABS_MOZC_SRC_DIR.joinpath(
    'third_party_cache', 'jom_1_1_3.zip')


def is_windows() -> bool:
  """Returns true if the platform is Windows."""
  return os.name == 'nt'


def is_mac() -> bool:
  """Returns true if the platform is Mac."""
  return os.name == 'posix' and os.uname()[0] == 'Darwin'


def is_linux() -> bool:
  """Returns true if the platform is Linux."""
  return os.name == 'posix' and os.uname()[0] == 'Linux'


class ProgressPrinter:
  """A utility to print progress message with carriage return and trancatoin."""

  def __enter__(self):
    if not sys.stdout.isatty():

      class NoOpImpl:
        """A no-op implementation in case stdout is not attached to concole."""

        def print_line(self, msg: str) -> None:
          """No-op implementation.

          Args:
            msg: Unused.
          """
          del msg  # Unused
          return

      self.cleaner = None
      return NoOpImpl()

    class Impl:
      """A real implementation in case stdout is attached to concole."""
      last_output_time_ns = time.time_ns()

      def print_line(self, msg: str) -> None:
        """Print the given message with carriage return and trancatoin.

        Args:
          msg: Message to be printed.
        """
        colmuns = os.get_terminal_size().columns
        now = time.time_ns()
        if (now - self.last_output_time_ns) < 25000000:
          return
        msg = msg + ' ' * max(colmuns - len(msg), 0)
        msg = msg[0 : (colmuns)] + '\r'
        sys.stdout.write(msg)
        sys.stdout.flush()
        self.last_output_time_ns = now

    class Cleaner:
      def cleanup(self) -> None:
        colmuns = os.get_terminal_size().columns
        sys.stdout.write(' ' * colmuns + '\r')
        sys.stdout.flush()

    self.cleaner = Cleaner()
    return Impl()

  def __exit__(self, *exc):
    if self.cleaner:
      self.cleaner.cleanup()


def qt_extract_filter(
    members: Iterator[tarfile.TarInfo],
) -> Iterator[tarfile.TarInfo]:
  """Custom extract filter for the Qt Tar file.

  This custom filter can be used to adjust directory structure and drop
  unnecessary files/directories to save disk space.

  Args:
    members: an iterator of TarInfo from the Tar file.

  Yields:
    An iterator of TarInfo to be extracted.
  """
  with ProgressPrinter() as printer:
    for info in members:
      paths = info.name.split('/')
      if '..' in paths:
        continue
      if len(paths) < 1:
        continue
      paths = paths[1:]
      new_path = '/'.join(paths)
      if len(paths) >= 1 and paths[0] == 'examples':
        printer.print_line('skipping   ' + new_path)
        continue
      else:
        printer.print_line('extracting ' + new_path)
        info.name = new_path
        yield info


@dataclasses.dataclass
@functools.total_ordering
class QtVersion:
  """Data type for Qt version.

  Attributes:
    major: Major version.
    minor: Minor version.
    patch: Patch level.
  """
  major: int
  minor: int
  patch: int

  def __hash__(self) -> int:
    return hash(self.major, self.minor, self.patch)

  def __eq__(self, other: Any) -> bool:
    if not isinstance(other, QtVersion):
      return NotImplemented
    return (
        self.major == other.major
        and self.minor == other.minor
        and self.patch == other.patch
    )

  def __lt__(self, other: Any) -> bool:
    if not isinstance(other, QtVersion):
      return NotImplemented
    if self.major != other.major:
      return self.major < other.major
    if self.minor != other.minor:
      return self.minor < other.minor
    return self.patch < other.patch


def get_qt_version(args: argparse.Namespace) -> QtVersion:
  """Get the Qt version.

  Args:
    args: build options to be used to customize configure options of Qt.

  Returns:
    QtVersion object.
  """
  archive_name = pathlib.Path(args.qt_archive_path).resolve().name
  ver_string_tuple = (
      archive_name
      .removeprefix('qtbase-everywhere-opensource-src-')
      .removeprefix('qtbase-everywhere-src-')
      .removesuffix('.tar.xz')
      .split('.')
  )
  if len(ver_string_tuple) != 3:
    raise ValueError(f'Unsupported qt archive name: {archive_name}')
  return QtVersion(
      major=int(ver_string_tuple[0]),
      minor=int(ver_string_tuple[1]),
      patch=int(ver_string_tuple[2]),
  )


def make_configure_options(args: argparse.Namespace) -> list[str]:
  """Makes necessary configure options based on args.

  Args:
    args: build options to be used to customize configure options of Qt.

  Returns:
    A list of configure options to be passed to configure of Qt.
  """

  qt_version = get_qt_version(args)

  qt_configure_options = ['-opensource',
                          '-silent',
                          '-no-cups',
                          '-no-dbus',
                          '-no-feature-concurrent',
                          '-no-feature-imageformatplugin',
                          '-no-feature-network',
                          '-no-feature-sql',
                          '-no-feature-sqlmodel',
                          '-no-feature-testlib',
                          '-no-feature-xml',
                          '-no-icu',
                          '-no-opengl',
                          '-no-sql-db2',
                          '-no-sql-ibase',
                          '-no-sql-mysql',
                          '-no-sql-oci',
                          '-no-sql-odbc',
                          '-no-sql-psql',
                          '-no-sql-sqlite',
                          '-nomake', 'examples',
                          '-nomake', 'tests',
                         ]
  if qt_version.major == 5:
    qt_configure_options += ['-no-sql-sqlite2', '-no-sql-tds']

  if is_mac():
    qt_configure_options += [
        '-platform', 'macx-clang',
        '-qt-libpng',
        '-qt-pcre',
    ]
  elif is_windows():
    qt_configure_options += ['-force-debug-info',
                             '-ltcg',  # Note: ignored in debug build
                             '-no-freetype',
                             '-no-harfbuzz',
                             '-platform', 'win32-msvc']
    if qt_version.major == 5:
      qt_configure_options += ['-no-angle', '-no-direct2d']
  if args.confirm_license:
    qt_configure_options += ['-confirm-license']

  if args.debug and args.release:
    qt_configure_options += ['-debug-and-release']
  elif args.debug:
    qt_configure_options += ['-debug']
  elif args.release:
    qt_configure_options += ['-release']

  qt_src_dir = pathlib.Path(args.qt_src_dir).resolve()
  qt_dest_dir = pathlib.Path(args.qt_dest_dir).resolve()
  if qt_src_dir != qt_dest_dir:
    qt_configure_options += ['-prefix', str(qt_dest_dir)]

  return qt_configure_options


def parse_args() -> argparse.Namespace:
  """Parse command line options."""
  parser = argparse.ArgumentParser()
  parser.add_argument('--debug', dest='debug', default=False,
                      action='store_true',
                      help='make debug build')
  parser.add_argument('--release', dest='release', default=False,
                      action='store_true',
                      help='make release build')
  parser.add_argument('--qt_src_dir', help='qt src directory', type=str,
                      default=str(ABS_QT_SRC_DIR))
  parser.add_argument('--qt_archive_path', help='qtbase archive path', type=str,
                      default=str(ABS_QT_ARCHIVE_PATH))
  parser.add_argument('--jom_archive_path', help='qtbase archive path',
                      type=str, default=str(ABS_JOM_ARCHIVE_PATH))
  parser.add_argument('--qt_dest_dir', help='qt dest directory', type=str,
                      default=str(ABS_QT_DEST_DIR))
  parser.add_argument('--confirm_license',
                      help='set to accept Qt OSS license',
                      action='store_true', default=False)
  parser.add_argument('--dryrun', action='store_true', default=False)
  if is_windows():
    parser.add_argument('--vcvarsall_path', help='Path of vcvarsall.bat',
                        type=str, default=None)
  return parser.parse_args()


def patch_file(file: pathlib.Path, pattern: str, replaced: str,
               dryrun: bool = False) -> None:
  """Apply local patch(es) to the specified file.

  Args:
    file: file to be patched.
    pattern: regex pattern to be matched.
    replaced: string to be replaced with.
    dryrun: True to do as a dry run.
  """
  content = file.read_text()
  new_content = re.sub(pattern, replaced, content, flags=re.MULTILINE)
  if content != new_content:
    if dryrun:
      print(f'dryrun: patchint {file}')
    else:
      file.write_text(new_content)


def build_on_mac(args: argparse.Namespace) -> None:
  """Build Qt from the source code on Mac.


  Args:
    args: build options to be used to customize configure options of Qt.
  Raises:
    FileNotFoundError: when any required file is not found.
  """
  qt_src_dir = pathlib.Path(args.qt_src_dir).resolve()
  qt_dest_dir = pathlib.Path(args.qt_dest_dir).resolve()

  if not qt_src_dir.exists():
    raise FileNotFoundError('Could not find qt_src_dir=%s' % qt_src_dir)

  os.chdir(qt_src_dir)

  configure_cmds = ['./configure'] + make_configure_options(args)
  if get_qt_version(args).major == 5:
    build_cmds = ['make', '--jobs=%s' % (os.cpu_count() * 2)]
    install_cmds = ['make', 'install']
  else:
    build_cmds = ['cmake', '--build', '.', '--parallel']
    install_cmds = ['cmake', '--install', '.']
  if args.dryrun:
    print(f'dryrun: run_or_die({configure_cmds})')
    print('dryrun: run_or_die({build_cmds})')
    if qt_src_dir != qt_dest_dir:
      if qt_dest_dir.exists():
        print(f'dryrun: delete {qt_dest_dir}')
      print('dryrun: run_or_die({install_cmds})')
  else:
    run_or_die(configure_cmds)

    run_or_die(build_cmds)
    if qt_src_dir != qt_dest_dir:
      if qt_dest_dir.exists():
        shutil.rmtree(qt_dest_dir)
      run_or_die(install_cmds)


def get_vcvarsall(path_hint: Union[str, None] = None) -> pathlib.Path:
  """Returns the path of 'vcvarsall.bat'.

  Args:
    path_hint: optional path to vcvarsall.bat

  Returns:
    The path of 'vcvarsall.bat'.

  Raises:
    FileNotFoundError: When 'vcvarsall.bat' cannot be found.
  """
  if path_hint is not None:
    path = pathlib.Path(path_hint).resolve()
    if path.exists():
      return path

  for program_files in ['Program Files', 'Program Files (x86)']:
    for edition in ['Community', 'Professional', 'Enterprise', 'BuildTools']:
      vcvarsall = pathlib.Path('C:\\', program_files, 'Microsoft Visual Studio',
                               '2022', edition, 'VC', 'Auxiliary', 'Build',
                               'vcvarsall.bat')
      if vcvarsall.exists():
        return vcvarsall

  raise FileNotFoundError(
      'Could not find vcvarsall.bat. '
      'Consider using --vcvarsall_path option e.g.\n'
      'python build_qt.py --release --confirm_license '
      r' --vcvarsall_path=C:\VS\VC\Auxiliary\Build\vcvarsall.bat'
  )


def get_vs_env_vars(
    arch: str,
    vcvarsall_path_hint: Union[str, None] = None,
) -> dict[str, str]:
  """Returns environment variables for the specified Visual Studio C++ tool.

  Oftentimes commandline build process for Windows requires to us to set up
  environment variables (especially 'PATH') first by executing an appropriate
  Visual C++ batch file ('vcvarsall.bat').  As a result, commands to be passed
  to methods like subprocess.run() can easily become complicated and difficult
  to maintain.

  With get_vs_env_vars() you can easily decouple environment variable handling
  from the actual command execution as follows.

    cwd = ...
    env = get_vs_env_vars('amd64_x86')
    subprocess.run(command, shell=True, check=True, cwd=cwd, env=env)

  or

    cwd = ...
    env = get_vs_env_vars('amd64_x86')
    subprocess.run(command_fullpath, shell=False, check=True, cwd=cwd, env=env)

  For the 'arch' argument, see the following link to find supported values.
  https://learn.microsoft.com/en-us/cpp/build/building-on-the-command-line#vcvarsall-syntax

  Args:
    arch: The architecture to be used to build Qt, e.g. 'amd64_x86'
    vcvarsall_path_hint: optional path to vcvarsall.bat

  Returns:
    A dict of environment variable.

  Raises:
    ChildProcessError: When 'vcvarsall.bat' cannot be executed.
    FileNotFoundError: When 'vcvarsall.bat' cannot be found.
  """
  vcvarsall = get_vcvarsall(vcvarsall_path_hint)

  pycmd = (r'import json;'
           r'import os;'
           r'print(json.dumps(dict(os.environ), ensure_ascii=True))')
  cmd = f'("{vcvarsall}" {arch}>nul)&&("{sys.executable}" -c "{pycmd}")'
  process = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
  stdout, _ = process.communicate()
  exitcode = process.wait()
  if exitcode != 0:
    raise ChildProcessError(f'Failed to execute {vcvarsall}')
  return json.loads(stdout.decode('ascii'))


def build_on_windows(args: argparse.Namespace) -> None:
  """Build Qt from the source code on Windows.

  Args:
    args: build options to be used to customize configure options of Qt.

  Raises:
    FileNotFoundError: when any required file is not found.
  """
  qt_src_dir = pathlib.Path(args.qt_src_dir).resolve()
  qt_dest_dir = pathlib.Path(args.qt_dest_dir).resolve()

  if not qt_src_dir.exists():
    raise FileNotFoundError('Could not find qt_src_dir=%s' % qt_src_dir)

  patch_file(
      qt_src_dir.joinpath('mkspecs', 'win32-msvc', 'qmake.conf'),
      '^load\\(qt_config\\)',
      '\n'.join([
          # Hide PDB path (b/1507329)
          'QMAKE_LFLAGS_RELEASE_WITH_DEBUGINFO += /PDBALTPATH:%_PDB%',
          # Hide PDB path (b/1507329)
          'QMAKE_LFLAGS_DEBUG += /PDBALTPATH:%_PDB%',
          'load(qt_config)',
      ]),
      args.dryrun)

  env = get_vs_env_vars('amd64_x86', args.vcvarsall_path)

  options = make_configure_options(args)
  configs = ' '.join(options)
  configure_cmds = f'configure.bat {configs}'
  if args.dryrun:
    print(f"dryrun: subprocess.run('{configure_cmds}', shell=True, check=True,"
          f' cwd={configure_cmds}, env={env})')
  else:
    subprocess.run(configure_cmds, shell=True, check=True, cwd=qt_src_dir,
                   env=env)

  # In order not to expose internal build path, replace paths in qconfig.cpp,
  # which have been embedded by configure.exe based on the build directory.
  # See b/2202493 for details.
  patch_file(
      qt_src_dir.joinpath('src', 'corelib', 'global', 'qconfig.cpp'),
      str(qt_dest_dir).replace('\\', '/'),
      'C:/qtbase',
      args.dryrun)

  if get_qt_version(args).major == 5:
    jom = qt_src_dir.joinpath('jom.exe')
    build_cmds = [str(jom)]
    install_cmds = [str(jom), 'install']
  else:
    build_cmds = ['cmake.exe', '--build', '.', '--parallel']
    install_cmds = ['cmake.exe', '--install', '.']
  if args.dryrun:
    print(f'dryrun: subprocess.run({build_cmds}, shell=True, check=True,'
          f' cwd={qt_src_dir}, env={env})')
    if qt_src_dir != qt_dest_dir:
      if qt_dest_dir.exists():
        print(f'dryrun: delete {qt_dest_dir}')
      print(f'dryrun: subprocess.run({install_cmds}, shell=True,'
            f' check=True, cwd={qt_src_dir}, env={env})')
  else:
    subprocess.run(build_cmds, shell=True, check=True, cwd=qt_src_dir, env=env)
    if qt_src_dir != qt_dest_dir:
      if qt_dest_dir.exists():
        shutil.rmtree(qt_dest_dir)
      subprocess.run(install_cmds, shell=True, check=True,
                     cwd=qt_src_dir, env=env)


def run_or_die(
    argv: list[Union[str, pathlib.Path]],
    env: Union[dict[str, str], None] = None,
) -> None:
  """Run the command, or die if it failed."""

  # Rest are the target program name and the parameters, but we special
  # case if the target program name ends with '.py'
  if pathlib.Path(argv[0]).suffix == '.py':
    argv.insert(0, sys.executable)  # Inject the python interpreter path.

  print('Running: ' + ' '.join([str(arg) for arg in argv]))
  try:
    subprocess.run(argv, check=True, env=env)
  except subprocess.CalledProcessError as e:
    print(e.output.decode('utf-8'))
    sys.exit(e.returncode)
  print('Done.')


def extract_qt_src(args: argparse.Namespace) -> None:
  """Extract Qt src from the archive.

  Args:
    args: build options to be used to customize configure options of Qt.
  """
  qt_src_dir = pathlib.Path(args.qt_src_dir).absolute()
  if qt_src_dir.exists():
    if args.dryrun:
      print(f'dryrun: delete {qt_src_dir}')
    else:
      shutil.rmtree(qt_src_dir)
  if args.dryrun:
    print(f'dryrun: extracting {args.qt_archive_path} to {qt_src_dir}')
    if is_windows():
      print(f'dryrun: extracting {args.jom_archive_path} to {qt_src_dir}')
  else:
    qt_src_dir.mkdir(parents=True)
    with tarfile.open(args.qt_archive_path, mode='r|xz') as f:
      f.extractall(path=qt_src_dir, members=qt_extract_filter(f))
    if is_windows():
      with zipfile.ZipFile(args.jom_archive_path) as z:
        z.extractall(path=qt_src_dir)


def main():
  if is_linux():
    print('On Linux, please use shared library provided by distributions.')
    sys.exit(1)

  args = parse_args()

  if not (args.debug or args.release):
    print('neither --release nor --debug is specified.')
    sys.exit(1)

  extract_qt_src(args)

  if is_mac():
    build_on_mac(args)
  elif is_windows():
    build_on_windows(args)


if __name__ == '__main__':
  main()
