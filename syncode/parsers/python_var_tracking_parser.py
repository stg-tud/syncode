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

    def get_vars(
        self, values: Iterable[Token | Tree], next_name_type: str | None = None
    ) -> list[tuple[str, str]]:
        if next_name_type is None:
            next_name_type = "USE"
        vars: list[tuple[str, str]] = []

        for it in values:
            if isinstance(it, Token):
                if it.type == "RULE":
                    # if it.value == "name_define":
                    #     next_name_type = "USE"
                    # el
                    if it.value in (
                        "function_name",
                        "param_var_name",
                        "except_exception_name",
                        "with_as_expr",
                        "def_expr_list",
                        "assign_expr",
                        "assign_exprs",
                    ):
                        next_name_type = "DEFINE"
                        print("found Rule:", it.value)
                elif it.type == "NAME":
                    vars.append((it.value, next_name_type))

            elif isinstance(it, Tree):
                data = it.data

                if isinstance(data, Token):
                    vars += self.get_vars([data, *it.children], next_name_type)
                else:
                    if isinstance(data, str) and data in (
                        "assign_exprs",
                        "assign_expr",
                    ):
                        next_name_type = "DEFINE"
                    vars += self.get_vars(it.children, next_name_type)

        return vars

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

        vars: list[tuple[str, str]] = []

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
                        print(parser_state.value_stack)
                        vars = self.get_vars(parser_state.value_stack)
                    else:
                        print(
                            f"Warning: Expected ParserState, got {type(parser_state)} for key {key}"
                        )
                else:
                    print(f"No stored state found for key {key}")

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

        print("vars:", vars)

        return lexer_tokens, lexing_incomplete
