# coding=utf-8
"""Tests for annotate."""
# Copyright 2017 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import _ast
import ast
import difflib
import inspect
import io
import itertools
import os.path
from six import with_metaclass
import sys
import textwrap
from typing import Tuple
from typed_ast import ast27
from typed_ast import ast3
import unittest

import pasta
from pasta.base import annotate
from pasta.base import ast_utils
from pasta.base import codegen
from pasta.base import formatting as fmt
from pasta.base import test_utils

# BEGIN GOOGLE
from absl import flags
FLAGS = flags.FLAGS
# END GOOGLE

# BEGIN GOOGLE
# TESTDATA_DIR = os.path.realpath(
#     os.path.join(os.path.dirname(pasta.__file__), '../testdata'))
TESTDATA_DIR = os.path.realpath(
    os.path.join(os.path.dirname(pasta.__file__), '../pasta/testdata'))
# END GOOGLE


def suite(py_ver: Tuple[int, int]):

  class PrefixSuffixTest(test_utils.TestCase):

    def test_block_suffix(self):
      src_tpl = textwrap.dedent("""\
          {open_block}
            pass #a
            #b
              #c

            #d
          #e
          a
          """)
      test_cases = (
          # first: attribute of the node with the last block
          # second: code snippet to open a block
          ('body', 'def x():'),
          ('body', 'class X:'),
          ('body', 'if x:'),
          ('orelse', 'if x:\n  y\nelse:'),
          ('body', 'if x:\n  y\nelif y:'),
          ('body', 'while x:'),
          ('orelse', 'while x:\n  y\nelse:'),
          ('finalbody', 'try:\n  x\nfinally:'),
          ('body', 'try:\n  x\nexcept:'),
          ('orelse', 'try:\n  x\nexcept:\n  y\nelse:'),
          ('body', 'with x:'),
          ('body', 'with x, y:'),
          ('body', 'with x:\n with y:'),
          ('body', 'for x in y:'),
      )

      def is_node_for_suffix(node, children_attr):
        # Return True if this node contains the 'pass' statement
        val = getattr(node, children_attr, None)
        return isinstance(val, list) and (type(val[0]) == ast27.Pass or
                                          type(val[0]) == ast3.Pass)

      for children_attr, open_block in test_cases:
        src = src_tpl.format(open_block=open_block)
        t = pasta.parse(src, py_ver)
        node_finder = ast_utils.get_find_node_visitor(
            lambda node: is_node_for_suffix(node, children_attr), py_ver)
        node_finder.visit(t)
        node = node_finder.results[0]
        expected = '  #b\n    #c\n\n  #d\n'
        actual = str(fmt.get(node, 'block_suffix_%s' % children_attr))
        self.assertMultiLineEqual(
            expected, actual,
            'Incorrect suffix for code:\n%s\nNode: %s (line %d)\nDiff:\n%s' %
            (src, node, node.lineno, '\n'.join(_get_diff(actual, expected))))
        self.assertMultiLineEqual(src, pasta.dump(t, py_ver))

    def test_module_suffix(self):
      src = 'foo\n#bar\n\n#baz\n'
      t = pasta.parse(src, py_ver)
      self.assertEqual(src[src.index('#bar'):], fmt.get(t, 'suffix'))

    def test_no_block_suffix_for_single_line_statement(self):
      src = 'if x:  return y\n  #a\n#b\n'
      t = pasta.parse(src, py_ver)
      self.assertIsNone(fmt.get(t.body[0], 'block_suffix_body'))

    def test_expression_prefix_suffix(self):
      src = 'a\n\nfoo\n\n\nb\n'
      t = pasta.parse(src, py_ver)
      self.assertEqual('\n', fmt.get(t.body[1], 'prefix'))
      self.assertEqual('\n', fmt.get(t.body[1], 'suffix'))

    def test_statement_prefix_suffix(self):
      src = 'a\n\ndef foo():\n  return bar\n\n\nb\n'
      t = pasta.parse(src, py_ver)
      self.assertEqual('\n', fmt.get(t.body[1], 'prefix'))
      self.assertEqual('', fmt.get(t.body[1], 'suffix'))

  class IndentationTest(test_utils.TestCase):

    def test_indent_levels(self):
      src = textwrap.dedent("""\
          foo('begin')
          if a:
            foo('a1')
            if b:
              foo('b1')
              if c:
                foo('c1')
              foo('b2')
            foo('a2')
          foo('end')
          """)
      t = pasta.parse(src, py_ver)
      call_nodes = ast_utils.find_nodes_by_type(t, (ast27.Call, ast3.Call),
                                                py_ver)
      call_nodes.sort(key=lambda node: node.lineno)
      begin, a1, b1, c1, b2, a2, end = call_nodes

      self.assertEqual('', fmt.get(begin, 'indent'))
      self.assertEqual('  ', fmt.get(a1, 'indent'))
      self.assertEqual('    ', fmt.get(b1, 'indent'))
      self.assertEqual('      ', fmt.get(c1, 'indent'))
      self.assertEqual('    ', fmt.get(b2, 'indent'))
      self.assertEqual('  ', fmt.get(a2, 'indent'))
      self.assertEqual('', fmt.get(end, 'indent'))

    def test_indent_levels_same_line(self):
      src = 'if a: b; c\n'
      t = pasta.parse(src, py_ver)
      if_node = t.body[0]
      b, c = if_node.body
      self.assertIsNone(fmt.get(b, 'indent_diff'))
      self.assertIsNone(fmt.get(c, 'indent_diff'))

    def test_indent_depths(self):
      template = 'if a:\n{first}if b:\n{first}{second}foo()\n'
      indents = (' ', ' ' * 2, ' ' * 4, ' ' * 8, '\t', '\t' * 2)

      for first, second in itertools.product(indents, indents):
        src = template.format(first=first, second=second)
        t = pasta.parse(src, py_ver)
        outer_if_node = t.body[0]
        inner_if_node = outer_if_node.body[0]
        call_node = inner_if_node.body[0]

        self.assertEqual('', fmt.get(outer_if_node, 'indent'))
        self.assertEqual('', fmt.get(outer_if_node, 'indent_diff'))
        self.assertEqual(first, fmt.get(inner_if_node, 'indent'))
        self.assertEqual(first, fmt.get(inner_if_node, 'indent_diff'))
        self.assertEqual(first + second, fmt.get(call_node, 'indent'))
        self.assertEqual(second, fmt.get(call_node, 'indent_diff'))

    def test_indent_multiline_string(self):
      src = textwrap.dedent('''\
          class A:
            """Doc
               string."""
            pass
          ''')
      t = pasta.parse(src, py_ver)
      docstring, pass_stmt = t.body[0].body
      self.assertEqual('  ', fmt.get(docstring, 'indent'))
      self.assertEqual('  ', fmt.get(pass_stmt, 'indent'))

    def test_indent_multiline_string_with_newline(self):
      src = textwrap.dedent('''\
          class A:
            """Doc\n
               string."""
            pass
          ''')
      t = pasta.parse(src, py_ver)
      docstring, pass_stmt = t.body[0].body
      self.assertEqual('  ', fmt.get(docstring, 'indent'))
      self.assertEqual('  ', fmt.get(pass_stmt, 'indent'))

    def test_scope_trailing_comma(self):
      template = 'def foo(a, b{trailing_comma}): pass'
      for trailing_comma in ('', ',', ' , '):
        tree = pasta.parse(
            template.format(trailing_comma=trailing_comma), py_ver)
        self.assertEqual(
            trailing_comma.lstrip(' ') + ')',
            fmt.get(tree.body[0], 'args_suffix'))

      template = 'class Foo(a, b{trailing_comma}): pass'
      for trailing_comma in ('', ',', ' , '):
        tree = pasta.parse(
            template.format(trailing_comma=trailing_comma), py_ver)
        self.assertEqual(
            trailing_comma.lstrip(' ') + ')',
            fmt.get(tree.body[0], 'bases_suffix'))

      template = 'from mod import (a, b{trailing_comma})'
      for trailing_comma in ('', ',', ' , '):
        tree = pasta.parse(
            template.format(trailing_comma=trailing_comma), py_ver)
        self.assertEqual(trailing_comma + ')',
                         fmt.get(tree.body[0], 'names_suffix'))

    def test_indent_extra_newlines(self):
      src = textwrap.dedent("""\
          if a:

            b
          """)
      t = pasta.parse(src, py_ver)
      if_node = t.body[0]
      b = if_node.body[0]
      self.assertEqual('  ', fmt.get(b, 'indent_diff'))

    def test_indent_extra_newlines_with_comment(self):
      src = textwrap.dedent("""\
          if a:
              #not here

            b
          """)
      t = pasta.parse(src, py_ver)
      if_node = t.body[0]
      b = if_node.body[0]
      self.assertEqual('  ', fmt.get(b, 'indent_diff'))

    def test_autoindent(self):
      src = textwrap.dedent("""\
          def a():
              b
              c
          """)
      expected = textwrap.dedent("""\
          def a():
              b
              new_node
          """)
      t = pasta.parse(src, py_ver)
      # Repace the second node and make sure the indent level is corrected
      if py_ver < (3, 0):
        t.body[0].body[1] = ast27.Expr(ast27.Name(id='new_node'))
      else:
        t.body[0].body[1] = ast3.Expr(ast3.Name(id='new_node'))
      self.assertMultiLineEqual(expected, codegen.to_str(t, py_ver))

    @test_utils.requires_features(['mixed_tabs_spaces'], py_ver)
    def test_mixed_tabs_spaces_indentation(self):
      pasta.parse(
          textwrap.dedent("""\
          if a:
                  b
          {ONETAB}c
          """).format(ONETAB='\t'), py_ver)

    @test_utils.requires_features(['mixed_tabs_spaces'], py_ver)
    def test_tab_below_spaces(self):
      for num_spaces in range(1, 8):
        t = pasta.parse(
            textwrap.dedent("""\
            if a:
            {WS}if b:
            {ONETAB}c
            """).format(ONETAB='\t', WS=' ' * num_spaces), py_ver)
        node_c = t.body[0].body[0].body[0]
        self.assertEqual(fmt.get(node_c, 'indent_diff'), ' ' * (8 - num_spaces))

    @test_utils.requires_features(['mixed_tabs_spaces'], py_ver)
    def test_tabs_below_spaces_and_tab(self):
      for num_spaces in range(1, 8):
        t = pasta.parse(
            textwrap.dedent("""\
            if a:
            {WS}{ONETAB}if b:
            {ONETAB}{ONETAB}c
            """).format(ONETAB='\t', WS=' ' * num_spaces), py_ver)
        node_c = t.body[0].body[0].body[0]
        self.assertEqual(fmt.get(node_c, 'indent_diff'), '\t')

  def _is_syntax_valid(filepath: str, py_ver: Tuple[int, int]) -> bool:
    with io.open(filepath, 'r', encoding='UTF-8') as f:
      try:
        pasta.ast_parse(f.read(), py_ver)
      except SyntaxError:
        return False
    return True

  class SymmetricTestMeta(type):

    def __new__(mcs, name, bases, inst_dict):
      # Helper function to generate a test method
      def symmetric_test_generator(filepath):

        def test(self):
          with open(filepath, 'r') as handle:
            src = handle.read()
          t = ast_utils.parse(src, py_ver)
          annotator = annotate.get_ast_annotator(py_ver)(src)
          annotator.visit(t)
          self.assertMultiLineEqual(codegen.to_str(t, py_ver), src)
          self.assertEqual([], annotator.tokens._parens, 'Unmatched parens')

        return test

      # Add a test method for each input file
      test_method_prefix = 'test_symmetric_'
      data_dir = os.path.join(TESTDATA_DIR, 'ast')
      for dirpath, dirs, files in os.walk(data_dir):
        for filename in files:
          if filename.endswith('.in'):
            full_path = os.path.join(dirpath, filename)
            inst_dict[test_method_prefix + filename[:-3]] = unittest.skipIf(
                not _is_syntax_valid(full_path, py_ver),
                'Test contains syntax not supported by this version.',
            )(
                symmetric_test_generator(full_path))
      return type.__new__(mcs, name, bases, inst_dict)

  class SymmetricTest(with_metaclass(SymmetricTestMeta, test_utils.TestCase)):
    """Validates the symmetry property.

    After parsing + annotating a module, regenerating the source code for it
    should yield the same result.
    """

  def _get_node_identifier(node):
    for attr in ('id', 'name', 'attr', 'arg', 'module'):
      if isinstance(getattr(node, attr, None), str):
        return getattr(node, attr, '')
    return ''

  class PrefixSuffixGoldenTestMeta(type):

    def __new__(mcs, name, bases, inst_dict):
      # Helper function to generate a test method
      def golden_test_generator(input_file, golden_file):

        def test(self):
          with open(input_file, 'r') as handle:
            src = handle.read()
          t = ast_utils.parse(src, py_ver)
          annotator = annotate.get_ast_annotator(py_ver)(src)
          annotator.visit(t)

          def escape(s):
            return '' if s is None else s.replace('\n', '\\n')

          result = '\n'.join(
              '{0:12} {1:20} \tprefix=|{2}|\tsuffix=|{3}|\tindent=|{4}|'.format(
                  str((getattr(n, 'lineno', -1), getattr(n, 'col_offset', -1))),
                  type(n).__name__ + ' ' +
                  _get_node_identifier(n), escape(fmt.get(n, 'prefix')),
                  escape(fmt.get(n, 'suffix')), escape(fmt.get(n, 'indent')))
              for n in pasta.ast_walk(t, py_ver)) + '\n'

          # If specified, write the golden data instead of checking it
          if getattr(self, 'generate_goldens', False):
            # BEGIN GOOGLE
            local_testdata_dir = os.path.join(sys.argv[1], 'testdata')
            local_golden_file = golden_file.replace(TESTDATA_DIR,
                                                    local_testdata_dir)
            if not os.path.isdir(os.path.dirname(local_golden_file)):
              os.makedirs(os.path.dirname(local_golden_file))
            with open(local_golden_file, 'w') as f:
              f.write(result)
            print('Wrote: ' + local_golden_file)
            # END GOOGLE
            return

        try:
          with io.open(golden_file, 'r', encoding='UTF-8') as f:
            golden = f.read()
        except IOError:
          self.fail('Missing golden data.')

          self.assertMultiLineEqual(golden, result)

        return test

      # Add a test method for each input file
      test_method_prefix = 'test_golden_prefix_suffix_'
      data_dir = os.path.join(TESTDATA_DIR, 'ast')
      # BEGIN GOOGLE
      # Maintain a separate set of golden data for Google python
      #python_version = '%d.%d' % sys.version_info[:2]
      python_version = '%d.%d_google' % (sys.version_info.major,
                                         sys.version_info.minor)
      # END GOOGLE
      for dirpath, dirs, files in os.walk(data_dir):
        for filename in files:
          if filename.endswith('.in'):
            full_path = os.path.join(dirpath, filename)
            golden_path = os.path.join(dirpath, 'golden', python_version,
                                       filename[:-3] + '.out')
            inst_dict[test_method_prefix + filename[:-3]] = unittest.skipIf(
                not _is_syntax_valid(full_path, py_ver),
                'Test contains syntax not supported by this version.',
            )(
                golden_test_generator(full_path, golden_path))
      return type.__new__(mcs, name, bases, inst_dict)

  class PrefixSuffixGoldenTest(
      with_metaclass(PrefixSuffixGoldenTestMeta, test_utils.TestCase)):
    """Checks the prefix and suffix on each node in the AST.

    This uses golden files in testdata/ast/golden. To regenerate these files,
    run
    python setup.py test -s pasta.base.annotate_test.generate_goldens
    """

    maxDiff = None

  class ManualEditsTest(test_utils.TestCase):
    """Tests that we can handle ASTs that have been modified.

    Such ASTs may lack position information (lineno/col_offset) on some nodes.
    """

    def test_call_no_pos(self):
      """Tests that Call node traversal works without position information."""
      src = 'f(a)'
      t = pasta.parse(src, py_ver)
      node = ast_utils.find_nodes_by_type(t, (ast27.Call, ast3.Call), py_ver)[0]
      if py_ver < (3, 0):
        node.keywords.append(ast27.keyword(arg='b', value=ast27.Num(n=0)))
      else:
        node.keywords.append(ast3.keyword(arg='b', value=ast3.Num(n=0)))
      self.assertEqual('f(a, b=0)', pasta.dump(t, py_ver))

    def test_call_illegal_pos(self):
      """Tests that Call node traversal works even with illegal positions."""
      src = 'f(a)'
      t = pasta.parse(src, py_ver)
      node = ast_utils.find_nodes_by_type(t, (ast27.Call, ast3.Call), py_ver)[0]
      if py_ver < (3, 0):
        node.keywords.append(ast27.keyword(arg='b', value=ast27.Num(n=0)))
      else:
        node.keywords.append(ast3.keyword(arg='b', value=ast3.Num(n=0)))

      # This position would put b=0 before a, so it should be ignored.
      node.keywords[-1].value.lineno = 0
      node.keywords[-1].value.col_offset = 0

      self.assertEqual('f(a, b=0)', pasta.dump(t, py_ver))

  class FstringTest(test_utils.TestCase):
    """Tests fstring support more in-depth."""

    @test_utils.requires_features(['fstring'], py_ver)
    def test_fstring(self):
      src = 'f"a {b} c d {e}"'
      t = pasta.parse(src, py_ver)
      node = t.body[0].value
      self.assertEqual(
          fmt.get(node, 'content'),
          'f"a {__pasta_fstring_val_0__} c d {__pasta_fstring_val_1__}"')

    @test_utils.requires_features(['fstring'], py_ver)
    def test_fstring_escaping(self):
      src = 'f"a {{{b} {{c}}"'
      t = pasta.parse(src, py_ver)
      node = t.body[0].value
      self.assertEqual(
          fmt.get(node, 'content'), 'f"a {{{__pasta_fstring_val_0__} {{c}}"')

  class VersionSupportTest(test_utils.TestCase):

    def test_all_ast_nodes_supported(self):
      functions = inspect.getmembers(annotate.get_ast_annotator(py_ver))
      handled_nodes = {
          name[6:] for name, _ in functions if name.startswith('visit_')
      }

      def should_ignore_type(n):
        if not issubclass(n, ast27.AST) and not issubclass(n, ast3.AST):
          return True
        # Expression contexts are not visited since the have no formatting
        if hasattr(_ast, 'expr_context') and (issubclass(
            n, ast27.expr_context) or issubclass(n, ast3.expr_context)):
          return True
        return False

      ast_nodes = {
          name for name, member in inspect.getmembers(_ast, inspect.isclass)
          if not should_ignore_type(member)
      }
      ignored_nodes = {
          'AST',
          'Expression',
          'FunctionType',
          'Interactive',
          'MatMult',
          'Suite',
          'TypeIgnore',  # TODO: Support syntax for this?
          'boolop',
          'cmpop',
          'excepthandler',
          'expr',
          'mod',
          'operator',
          'slice',
          'stmt',
          'type_ignore',
          'unaryop',
      }
      self.assertEqual(set(), ast_nodes - handled_nodes - ignored_nodes)

  def _get_diff(before, after):
    return difflib.ndiff(after.splitlines(), before.splitlines())

  def generate_goldens():
    result = unittest.TestSuite()
    result.addTests(unittest.makeSuite(PrefixSuffixGoldenTest))
    setattr(PrefixSuffixGoldenTest, 'generate_goldens', True)
    return result

  result = unittest.TestSuite()
  result.addTests(unittest.makeSuite(ManualEditsTest))
  result.addTests(unittest.makeSuite(SymmetricTest))
  result.addTests(unittest.makeSuite(PrefixSuffixTest))
  result.addTests(unittest.makeSuite(PrefixSuffixGoldenTest))
  result.addTests(unittest.makeSuite(FstringTest))
  result.addTests(unittest.makeSuite(VersionSupportTest))
  return result

if __name__ == '__main__':
  unittest.main()
