
import logging
from typing import Iterable, Tuple
from syncode.larkm import Token
import syncode.larkm as lark



from syncode.parsers.python_parser import PythonIncrementalParser, PythonIndenter
logger = logging.getLogger(__name__)

class PythonVarTrackingIncrementalParser(PythonIncrementalParser):
    """
    This class implements an incremental parser for Python code, tracking variable defintions and uses.
    """

    def __init__(self, base_parser, indenter, partial_code=None,**kwargs):
        super().__init__(base_parser, indenter, partial_code,**kwargs)
        self._defined_vars: set[Token] = set()

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

        try:
            while lexer_state.line_ctr.char_pos < len(lexer_state.text):
                # PostLexConnector -> BasicLexer
                blexer = interactive.lexer_thread.lexer.lexer
                
                token = blexer.next_token(lexer_state)
                self.lexer_pos = lexer_state.line_ctr.char_pos

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
            pass # This may happen when the partial code has an ignore terminal
        except EOFError as e:
            pass

        return lexer_tokens, lexing_incomplete

