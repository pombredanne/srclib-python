from collections import namedtuple
import os
import jedi


class FileGrapherException(Exception):
    """ Something went wrong while graphing the file. """


class FileGrapher(object):
    """
    FileGrapher is used to extract definitions and references from single Python source file.
    """
    Def = namedtuple('Def', ['Path', 'Kind', 'Name', 'File', 'DefStart', 'DefEnd', 'Exported', 'Docstring', 'Data'])
    Ref = namedtuple('Ref', ['DefPath', 'DefFile', 'Def', 'File', 'Start', 'End', 'ToBuiltin'])

    def __init__(self, base_dir, source_file, log):
        """
        Create a new grapher.
        """
        self._base_dir = base_dir
        self._file = source_file
        # TODO: Can easily remove `log` to have cleaner class. Only used once.
        self._log = log
        self._source = None
        self._defs = {}
        self._refs = {}

    def graph(self):
        # TODO: Maybe move to `__init__`?
        self._load()

        # Add module/package defs.
        module = os.path.splitext(self._file)[0]
        if os.path.basename(self._file) == '__init__.py':
            module = os.path.normpath(os.path.dirname(self._file))

        self._add_def(self.Def(
            Path=module,
            Kind='module',
            Name=module.split('/')[-1],
            File=self._file,
            DefStart=0,
            DefEnd=0,
            Exported=True,
            # TODO: extract module/package-level doc.
            Docstring='',
            Data=None,
        ))

        # Jedi.
        try:
            jedi_names = jedi.names(source=self._source, path=self._file, all_scopes=True, references=True)
        except Exception as e:
            raise FileGrapherException('failed to parse {}: {}'.format(self._file, str(e)))

        jedi_defs, jedi_refs = [], []
        for jedi_name in jedi_names:
            if jedi_name.is_definition():
                    jedi_defs.append(jedi_name)
            else:
                jedi_refs.append(jedi_name)

        # Defs.
        for jedi_def in jedi_defs:
            if jedi_def.type != 'import':
                # Skip imports.
                continue
            self._add_def(self._jedi_def_to_def(jedi_def))

        # Refs.
        for jedi_ref in jedi_refs:
            # noinspection PyBroadException
            try:
                ref_defs = jedi_ref.goto_assignments()
            except Exception:
                self._log.error('error getting definitions for reference {}'.format(str(jedi_ref)[0:50]))
                continue

            if len(ref_defs) == 0:
                continue

            sg_def = self._jedi_def_to_def_key(ref_defs[0])
            ref_start = self._to_offset(jedi_ref.line, jedi_ref.column)
            ref_end = ref_start + len(jedi_ref.name)
            self._add_ref(self.Ref(
                DefPath=sg_def.Path,
                DefFile=sg_def.File,
                Def=False,
                File=self._file,
                Start=ref_start,
                End=ref_end,
                ToBuiltin=ref_defs[0].in_builtin_module(),
                # ToBuiltin='sg_def.Kind={}, ref.Kind={}'.format(sg_def.Kind, jedi_ref.type),
            ))

        return self._defs, self._refs

    def _load(self):
        """ Load file in memory. """
        with open(self._file) as f:
            self._source = f.read()

        # TODO: Does this work with windows style newlines?
        source_lines = self._source.split('\n')
        self._cumulative_off = [0]
        for line in source_lines:
            self._cumulative_off.append(self._cumulative_off[-1] + len(line) + 1)

    def _jedi_def_to_def(self, d):
        # TODO: Add back this if needed:
        # If def is a name, then the location of the definition is the last name part
        start = self._to_offset(d.line, d.column)
        end = start + len(d.name)

        return self.Def(
            Path=self._full_name(d).replace('.', '/'),
            Kind=d.type,
            Name=d.name,
            File=self._file,
            DefStart=start,
            DefEnd=end,
            # TODO: not all vars are exported
            Exported=True,
            Docstring=d.docstring(),
            Data=None,
        )

    def _jedi_def_to_def_key(self, d):
        return self.Def(
            Path=self._full_name(d).replace('.', '/'),
            Kind=d.type,
            Name=d.name,
            File=d.module_path,
            DefStart=None,
            DefEnd=None,
            # TODO: not all vars are exported.
            Exported=True,
            Docstring=d.docstring(),
            Data=None,
        )

    def _full_name(self, d):
        if d.in_builtin_module():
            return d.name
        if d.type == 'statement' or d.type == 'param':
            name = '{}.{}'.format(d.full_name, d.name)
        else:
            name = d.full_name

        # module_path = os.path.relpath(d.module_path, self.base_dir)
        # if not d.is_definition():
        module_path = self._abs_module_path_to_relative_module_path(d.module_path)

        if os.path.basename(module_path) == '__init__.py':
            parent_module = os.path.dirname(os.path.dirname(module_path))
        else:
            parent_module = os.path.dirname(module_path)

        if parent_module == '':
            return name

        return '{}.{}'.format(parent_module, name)

    def _abs_module_path_to_relative_module_path(self, module_path):
        rel_path = os.path.relpath(module_path, self._base_dir)
        if not rel_path.startswith('..'):
            return rel_path

        components = module_path.split(os.sep)
        pi1 = pi2 = -1
        for i, component in enumerate(components):
            if component in ['site-packages', 'dist-packages']:
                pi1 = i
                break
            # Fallback.
            if pi2 == -1 and component.startswith('python'):
                pi2 = i

        pi = pi1 if pi1 != -1 else pi2
        if pi != -1:
            return os.path.join(*components[pi + 1:])

        raise FileGrapherException('could not convert absolute module path {} '
                                   'to relative module path'.format(module_path))

    def _add_def(self, d):
        """ Add definition, also adds self-reference. """
        if d.Path not in self._defs:
            self._defs[d.Path] = d
            # Add self-reference.
            if d.Kind != "module":
                self._add_ref(self.Ref(
                    DefPath=d.Path,
                    DefFile=os.path.abspath(d.File),
                    Def=True,
                    File=d.File,
                    Start=d.DefStart,
                    End=d.DefEnd,
                    ToBuiltin=False,
                ))

    def _add_ref(self, r):
        """ Add reference. """
        key = (r.DefPath, r.DefFile, r.File, r.Start, r.End)
        if key not in self._refs:
            self._refs[key] = r

    def _to_offset(self, line, column):
        """
        Converts from (line, col) position to byte offset.
        Line is 1-indexed, column is 0-indexed.
        """
        line -= 1
        if line >= len(self._cumulative_off):
            raise FileGrapherException('requested line out of bounds {} > {}'.format(
                line + 1,
                len(self._cumulative_off) - 1)
            )
        return self._cumulative_off[line] + column