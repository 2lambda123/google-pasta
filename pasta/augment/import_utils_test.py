# coding=utf-8
"""Tests for import_utils."""
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

import ast
import traceback
import unittest

from pasta.augment import import_utils
from pasta.base import ast_utils
from pasta.base import test_utils
from pasta.base import scope


class SplitImportTest(test_utils.TestCase):

  def test_split_normal_import(self):
    src = 'import aaa, bbb, ccc\n'
    t = ast.parse(src)
    import_node = t.body[0]
    sc = scope.analyze(t)
    import_utils.split_import(sc, import_node, import_node.names[1])

    self.assertEqual(2, len(t.body))
    self.assertEqual(ast.Import, type(t.body[1]))
    self.assertEqual([alias.name for alias in t.body[0].names], ['aaa', 'ccc'])
    self.assertEqual([alias.name for alias in t.body[1].names], ['bbb'])

  def test_split_from_import(self):
    src = 'from aaa import bbb, ccc, ddd\n'
    t = ast.parse(src)
    import_node = t.body[0]
    sc = scope.analyze(t)
    import_utils.split_import(sc, import_node, import_node.names[1])

    self.assertEqual(2, len(t.body))
    self.assertEqual(ast.ImportFrom, type(t.body[1]))
    self.assertEqual(t.body[0].module, 'aaa')
    self.assertEqual(t.body[1].module, 'aaa')
    self.assertEqual([alias.name for alias in t.body[0].names], ['bbb', 'ddd'])
 
  def test_split_imports_with_alias(self):
    src = 'import aaa as a, bbb as b, ccc as c\n'
    t = ast.parse(src)
    import_node = t.body[0]
    sc = scope.analyze(t)
    import_utils.split_import(sc, import_node, import_node.names[1])

    self.assertEqual(2, len(t.body))
    self.assertEqual([alias.name for alias in t.body[0].names], ['aaa', 'ccc'])
    self.assertEqual([alias.name for alias in t.body[1].names], ['bbb'])
    self.assertEqual(t.body[1].names[0].asname, 'b')
 
  def test_split_imports_multiple(self):
    src = 'import aaa, bbb, ccc\n'
    t = ast.parse(src)
    import_node = t.body[0]
    alias_bbb = import_node.names[1]
    alias_ccc = import_node.names[2]
    sc = scope.analyze(t)
    import_utils.split_import(sc, import_node, alias_bbb)
    import_utils.split_import(sc, import_node, alias_ccc)

    self.assertEqual(3, len(t.body))
    self.assertEqual([alias.name for alias in t.body[0].names], ['aaa'])
    self.assertEqual([alias.name for alias in t.body[1].names], ['ccc'])
    self.assertEqual([alias.name for alias in t.body[2].names], ['bbb'])
 
  def test_split_nested_imports(self):
    test_cases = (
        'def foo():\n  {import_stmt}\n',
        'class Foo(object):\n  {import_stmt}\n',
        'if foo:\n  {import_stmt}\nelse:\n  pass\n',
        'if foo:\n  pass\nelse:\n  {import_stmt}\n',
        'if foo:\n  pass\nelif bar:\n  {import_stmt}\n',
        'try:\n  {import_stmt}\nexcept:\n  pass\n',
        'try:\n  pass\nexcept:\n  {import_stmt}\n',
        'try:\n  pass\nfinally:\n  {import_stmt}\n',
        'for i in foo:\n  {import_stmt}\n',
        'for i in foo:\n  pass\nelse:\n  {import_stmt}\n',
        'while foo:\n  {import_stmt}\n',
    )

    for template in test_cases:
      try:
        src = template.format(import_stmt='import aaa, bbb, ccc')
        t = ast.parse(src)
        sc = scope.analyze(t)
        import_node = ast_utils.find_nodes_by_type(t, ast.Import)[0]
        import_utils.split_import(sc, import_node, import_node.names[1])

        split_import_nodes = ast_utils.find_nodes_by_type(t, ast.Import)
        self.assertEqual(1, len(t.body))
        self.assertEqual(2, len(split_import_nodes))
        self.assertEqual([alias.name for alias in split_import_nodes[0].names],
                         ['aaa', 'ccc'])
        self.assertEqual([alias.name for alias in split_import_nodes[1].names],
                         ['bbb'])
      except:
        self.fail('Failed while executing case:\n%s\nCaused by:\n%s' % 
                  (src, traceback.format_exc()))

class GetUnusedImportsTest(test_utils.TestCase):

  def test_normal_imports(self):
    src = """\
import a
import b
a.foo()
"""
    tree = ast.parse(src)
    self.assertItemsEqual(import_utils.get_unused_import_aliases(tree),
                          [tree.body[1].names[0]])

  def test_import_from(self):
    src = """\
from my_module import a
import b
from my_module import c
b.foo()
c.bar()
"""
    tree = ast.parse(src)
    self.assertItemsEqual(import_utils.get_unused_import_aliases(tree),
                          [tree.body[0].names[0]])

  def test_import_from_alias(self):
    src = """\
from my_module import a, b
b.foo()
"""
    tree = ast.parse(src)
    self.assertItemsEqual(import_utils.get_unused_import_aliases(tree),
                          [tree.body[0].names[0]])

  def test_import_asname(self):
    src = """\
from my_module import a as a_mod, b as unused_b_mod
import c as c_mod, d as unused_d_mod
a_mod.foo()
c_mod.foo()
"""
    tree = ast.parse(src)
    self.assertItemsEqual(import_utils.get_unused_import_aliases(tree),
                          [tree.body[0].names[1],
                           tree.body[1].names[1]])

  def test_dynamic_import(self):
    # For now we just don't want to error out on these, longer
    # term we want to do the right thing (see
    # https://github.com/google/pasta/issues/32)
    src = """\
def foo():
  import bar
"""
    tree = ast.parse(src)
    self.assertItemsEqual(import_utils.get_unused_import_aliases(tree),
                          [])

    

class RemoveImportsTest(test_utils.TestCase):
  # Note that we don't test any 'asname' examples but as far as remove_import
  # is concerned its not a different case because its still just an alias type
  # and we don't care about the internals of the alias we're trying to remove.
  def test_remove_just_alias(self):
    src = "import a, b"
    tree = ast.parse(src)
    sc = scope.analyze(tree)

    unused_b_node = tree.body[0].names[1]

    import_utils.remove_import(sc, unused_b_node)

    self.assertEqual(len(tree.body), 1)
    self.assertEqual(type(tree.body[0]), ast.Import)
    self.assertEqual(len(tree.body[0].names), 1)
    self.assertEqual(tree.body[0].names[0].name, 'a')

  def test_remove_just_alias_import_from(self):
    src = "from m import a, b"
    tree = ast.parse(src)
    sc = scope.analyze(tree)

    unused_b_node = tree.body[0].names[1]

    import_utils.remove_import(sc, unused_b_node)

    self.assertEqual(len(tree.body), 1)
    self.assertEqual(type(tree.body[0]), ast.ImportFrom)
    self.assertEqual(len(tree.body[0].names), 1)
    self.assertEqual(tree.body[0].names[0].name, 'a')

  def test_remove_full_import(self):
    src = "import a"
    tree = ast.parse(src)
    sc = scope.analyze(tree)

    a_node = tree.body[0].names[0]

    import_utils.remove_import(sc, a_node)

    self.assertEqual(len(tree.body), 0)

  def test_remove_full_importfrom(self):
    src = "from m import a"
    tree = ast.parse(src)
    sc = scope.analyze(tree)

    a_node = tree.body[0].names[0]

    import_utils.remove_import(sc, a_node)

    self.assertEqual(len(tree.body), 0)


def suite():
  result = unittest.TestSuite()
  result.addTests(unittest.makeSuite(SplitImportTest))
  result.addTests(unittest.makeSuite(GetUnusedImportsTest))
  result.addTests(unittest.makeSuite(RemoveImportsTest))
  return result

if __name__ == '__main__':
  unittest.main()
