from typing import Iterable, Tuple
import syncode.larkm as lark
from syncode.larkm.lark import Lark
from syncode.larkm.lexer import Token
from syncode.parsers.incremental_parser import IncrementalParser


class TestingGrammarIncrementalParser(IncrementalParser):
    """Incremental parser for the testing grammar that can remember variable deffinitions.
    """

    def __init__(self, base_parser: Lark, ignore_whitespace=False) -> None:
        super().__init__(base_parser, ignore_whitespace)
        self._defined_vars: set[Token] = set()


    @property
    def defined_vars(self):
        """
        Returns the copy of the list of defined variables.
        """
        return [v for v in self._defined_vars]


    def _lex_code(self, code) -> Tuple[Iterable[Token], bool]:
        """
        Lexes the given code and returns the list of tokens.
        """
        # Collect Lexer tokens
        lexer_tokens: Iterable[Token] = []
        interactive = self.base_parser.parse_interactive(code)
        lexer_state = interactive.lexer_thread.state
        lexing_incomplete = False

        try:
            while lexer_state.line_ctr.char_pos < len(lexer_state.text):
                blexer = interactive.lexer_thread.lexer
                token = blexer.next_token(lexer_state)

                if token.type == "DEF_VAR_NAME":
                    self._defined_vars.add(token.value)

                self.lexer_pos = lexer_state.line_ctr.char_pos
                lexer_tokens.append(token)

        except lark.exceptions.UnexpectedCharacters as e:
            lexing_incomplete = True
            # We update the lexer position to the current position since the lexer has stopped at this position
            self.lexer_pos = lexer_state.line_ctr.char_pos

        except EOFError as e:
            pass

        return lexer_tokens, lexing_incomplete
