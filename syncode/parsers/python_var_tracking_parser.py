import enum
import logging
from typing import Iterable, Tuple

import regex
from syncode.larkm import Token
import syncode.larkm as lark


from syncode.larkm.parsers.lalr_parser_state import ParserState
from syncode.parsers.itergen_parser import IGParser
from syncode.parsers.python_parser import PythonIndenter
from syncode.larkm.tree import Tree

logger = logging.getLogger(__name__)


class VarUseType(enum.StrEnum):
    """
    Enum-like class to represent the type of variable use.
    """

    DEFINE = "DEFINE"
    USE = "USE"
    IGNORE = "IGNORE"


class TreeVisitor(lark.visitors.Visitor):
    def __init__(self):
        super().__init__()
        self.path: list[str] = []
        self.vars: list[tuple[str, VarUseType]] = []
        self.next_name_type: VarUseType = VarUseType.USE
        self._in_assign_expr = False
        self._in_lhs_assign = False

        for define_function in [
            "function_name",
            "class_name",
            "param_var_name",
            "except_exception_name",
            "with_as_expr",
            "def_expr_list",
        ]:
            setattr(self, define_function, self.default_define)

        # "assign_expr", "assign_exprs",
        for assign_function in [
            "assign_expr",
            "assign_exprs",
        ]:
            setattr(self, assign_function, self.default_define_expr)

    def visit_topdown(self, tree: Tree) -> Tree:
        "Visit the tree, starting at the root, and ending at the leaves (top-down)"
        self.path.append(tree.data)

        for subtree in [t for t in tree.children if isinstance(t, Tree)]:
            self._call_userfunc(subtree)

        self.path.pop()
        return tree

    def __default__(self, tree: Tree):
        for node in tree.children:
            if isinstance(node, Tree):
                self.path.append(tree.data)
                self._call_userfunc(node)
                self.path.pop()

            if isinstance(node, Token) and node.type == "NAME":
                ignore = self.next_name_type == VarUseType.IGNORE
                if not ignore:
                    self.vars.append((node.value, self.next_name_type))

    def default_define(self, tree: Tree):
        self.next_name_type = VarUseType.DEFINE
        self.__default__(tree)
        self.next_name_type = VarUseType.USE

    def default_define_expr(self, tree: Tree):
        self._in_assign_expr = True
        self.__default__(tree)
        self._in_assign_expr = False

    def testlist_star_expr(self, tree: Tree):
        if not self._in_assign_expr:
            return self.__default__(tree)

        self._in_assign_expr = False
        self._in_lhs_assign = True
        self.__default__(tree)
        self._in_lhs_assign = False

    def var(self, tree: Tree):
        # If we are in the lhs of an assigments
        if not self._in_lhs_assign:
            return self.__default__(tree)

        # and we did not come from getitem or getattr
        if self.path[-2] in ["getitem", "getattr"]:
            return self.__default__(tree)

        # the variable "usage" should be a define
        self.default_define(tree)

    def import_stmt(self, tree: Tree):
        self.next_name_type = VarUseType.IGNORE
        self.__default__(tree)
        self.next_name_type = VarUseType.USE

    def dotted_name(self, tree: Tree):
        var_use_backup = self.next_name_type
        if self.path[-1] == "import_from":
            self.next_name_type = VarUseType.IGNORE

        self.__default__(tree)
        self.next_name_type = var_use_backup

    def as_name(self, tree: Tree):
        next_name_type_backup = self.next_name_type
        self.next_name_type = VarUseType.DEFINE

        # import_from -> dotted_name -> as_name
        if self.path[-2:] == ["import_from", "dotted_name"]:
            self.next_name_type = VarUseType.IGNORE

        # No define (dotted_as_name -> dotted_name -> as_name) IFF dotted_as_name -> as_name exists
        if self.path and self.path[-1] == "dotted_as_name":
            self.vars = self.vars[:-1]  # remove last define

        self.__default__(tree)

        self.next_name_type = next_name_type_backup


class PythonVarTrackingIncrementalParser(IGParser):
    """
    This class implements an incremental parser for Python code, tracking variable defintions and uses.
    """

    def __init__(self, base_parser, indenter, partial_code=None, **kwargs):
        super().__init__(base_parser, ignore_whitespace=False)

        if partial_code is not None:  # extract indentation type from partial code
            indenter.tab_len = self._get_indentation(partial_code)
        self.tab_len = indenter.tab_len

        self._defined_vars: set[Token] = set()

    def _get_indentation(self, partial_code) -> int:
        m = regex.match(
            r"(.*?):(.*?)\n(.*?)(?![ \t])", partial_code, flags=regex.DOTALL
        )
        indent_type = m.group(3)
        tab_len = 4  # Default tab length
        if "\t" not in indent_type:  # that means we are using spaces for indentation
            tab_len = indent_type.count(" ")
        return tab_len

    @property
    def defined_vars(self):
        """
        Returns a copy of the list of defined variables.
        """
        return [v for v in self._defined_vars]

    def _lex_code(self, code: str) -> Tuple[Iterable[Token], bool]:
        # Collect Lexer tokens
        lexer_tokens: Iterable[Token] = []
        interactive = self.base_parser.parse_interactive(code)
        lexer_state = interactive.lexer_thread.state
        indenter: PythonIndenter = self.base_parser.lexer_conf.postlex
        lexing_incomplete = False

        # Reset the indentation level
        indenter.indent_level, indenter.paren_level = [0], 0

        self._defined_vars.clear()

        vars: list[tuple[str, VarUseType]] = []

        try:
            while lexer_state.line_ctr.char_pos < len(lexer_state.text):
                # PostLexConnector -> BasicLexer
                blexer = interactive.lexer_thread.lexer.lexer

                token = blexer.next_token(lexer_state)
                self.lexer_pos = lexer_state.line_ctr.char_pos

                # print(self.cur_pos_to_parser_state)
                current_tokens = lexer_tokens[
                    : len(lexer_tokens)
                ]  # All tokens processed so far
                key = self._get_hash(current_tokens)

                if key in self.cur_pos_to_parser_state:
                    stored_state = self.cur_pos_to_parser_state[key]
                    parser_state = stored_state[1]
                    if isinstance(parser_state, ParserState):
                        # print(parser_state.value_stack)
                        vars_visitor = TreeVisitor()
                        vars_visitor.visit_topdown(
                            Tree("ROOT", parser_state.value_stack)
                        )

                        vars = vars_visitor.vars
                    else:
                        logger.error(
                            f"Warning: Expected ParserState, got {type(parser_state)} for key {key}"
                        )

                if token.type == "NAME_DEFINE":
                    self._defined_vars.add(token.value)

                # Perform postlexing indentation
                if token.type == indenter.NL_type:
                    lexer_tokens += indenter._handle_NL(token)
                else:
                    lexer_tokens.append(token)
                if token.type in indenter.OPEN_PAREN_types:
                    indenter.paren_level += 1
                elif token.type in indenter.CLOSE_PAREN_types:
                    indenter.paren_level -= 1
                    assert indenter.paren_level >= 0
        except lark.exceptions.UnexpectedCharacters as e:
            lexing_incomplete = True
            pass  # This may happen when the partial code has an ignore terminal
        except EOFError as e:
            pass

        # print("vars:", vars)
        logger.debug("vars: %s", vars)

        return lexer_tokens, lexing_incomplete
