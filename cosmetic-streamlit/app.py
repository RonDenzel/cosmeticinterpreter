
import streamlit as st
import re
import json
from enum import Enum
from dataclasses import dataclass
from pathlib import Path
import firebase_admin
from firebase_admin import credentials, auth, db
from datetime import datetime
from typing import List, Dict, Any, Set, Optional
import os

# Page configuration
st.set_page_config(
    page_title="Cosmetic Asset Customization",
    page_icon="👗",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #FF1493;
        text-align: center;
        margin-bottom: 2rem;
    }
    .success-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #d4edda;
        border: 1px solid #c3e6cb;
        color: #155724;
        margin: 1rem 0;
    }
    .error-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #f8d7da;
        border: 1px solid #f5c6cb;
        color: #721c24;
        margin: 1rem 0;
    }
    .info-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #d1ecf1;
        border: 1px solid #bee5eb;
        color: #0c5460;
        margin: 1rem 0;
    }
    .command-box {
        background-color: #f8f9fa;
        padding: 1rem;
        border-radius: 0.5rem;
        border-left: 4px solid #FF1493;
        margin: 0.5rem 0;
    }
</style>
""", unsafe_allow_html=True)

# ==================== TOKEN & PARSER CLASSES ====================

class TokenType(Enum):
    COMMAND = "COMMAND"
    STRING_LITERAL = "STRING_LITERAL"
    EOF = "EOF"

@dataclass
class Token:
    type: TokenType
    value: str
    position: int

class CosmeticsTokenizer:
    COMMANDS = {
        'apply theme': TokenType.COMMAND,
        'add item': TokenType.COMMAND,
        'remove item': TokenType.COMMAND,
        'clear inventory': TokenType.COMMAND,
        'add item list': TokenType.COMMAND,
        'color palette': TokenType.COMMAND,
        'assemble cosmetic': TokenType.COMMAND,
    }

    def __init__(self, input_text: str):
        self.input = input_text.strip()
        self.position = 0
        self.tokens: List[Token] = []

    def tokenize(self) -> List[Token]:
        self.tokens = []
        self.position = 0

        command_token = self._match_command()
        if command_token:
            self.tokens.append(command_token)
            self._extract_string_literals()

        self.tokens.append(Token(TokenType.EOF, "", self.position))
        return self.tokens

    def _match_command(self) -> Optional[Token]:
        input_lower = self.input.lower()
        sorted_commands = sorted(self.COMMANDS.keys(), key=len, reverse=True)

        for cmd in sorted_commands:
            if input_lower.startswith(cmd):
                token = Token(TokenType.COMMAND, cmd, 0)
                self.position = len(cmd)
                return token
        return None

    def _extract_string_literals(self):
        remaining = self.input[self.position:]
        pattern = r"['\"]([^'\"]+)['\"]"
        matches = re.finditer(pattern, remaining)

        for match in matches:
            value = match.group(1).strip()
            abs_position = self.position + match.start()
            token = Token(TokenType.STRING_LITERAL, value, abs_position)
            self.tokens.append(token)

class ParseError(Exception):
    pass

@dataclass
class ASTNode:
    command: str
    arguments: List[str]

class CosmeticsParser:
    COMMAND_RULES = {
        'apply theme': (1, 1),
        'add item': (1, 1),
        'remove item': (1, 1),
        'clear inventory': (0, 0),
        'add item list': (1, None),
        'color palette': (1, None),
        'assemble cosmetic': (0, 0),
    }

    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.current = 0

    def parse(self) -> ASTNode:
        if not self.tokens or len(self.tokens) == 0:
            raise ParseError("No tokens to parse")

        if self.tokens[0].type != TokenType.COMMAND:
            raise ParseError(f"Expected COMMAND token, got {self.tokens[0].type}")

        command = self.tokens[0].value
        self.current = 1

        if command not in self.COMMAND_RULES:
            raise ParseError(f"Unknown command: '{command}'")

        arguments = []
        while self.current < len(self.tokens) and self.tokens[self.current].type == TokenType.STRING_LITERAL:
            arguments.append(self.tokens[self.current].value)
            self.current += 1

        self._validate_arguments(command, arguments)
        return ASTNode(command=command, arguments=arguments)

    def _validate_arguments(self, command: str, arguments: List[str]):
        min_args, max_args = self.COMMAND_RULES[command]
        arg_count = len(arguments)

        if arg_count < min_args:
            raise ParseError(
                f"Command '{command}' requires at least {min_args} argument(s), got {arg_count}"
            )

        if max_args is not None and arg_count > max_args:
            raise ParseError(
                f"Command '{command}' accepts at most {max_args} argument(s), got {arg_count}"
            )

# ==================== COSMETIC OUTFIT CLASS ====================

@dataclass
class CosmeticOutfit:
    name: str
    theme: str
    items: List[str]
    colors: List[str]
    image: str
    steps: List[str]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CosmeticOutfit':
        return cls(
            name=data['name'],
            theme=data['theme'],
            items=data['items'],
            colors=data['colors'],
            image=data['image'],
            steps=data.get('steps', [])
        )

@dataclass
class OutfitMatch:
    outfit: CosmeticOutfit
    missing_items: Set[str]
    missing_colors: Set[str]
    color_overlap: int
    match_score: float

# ==================== FIREBASE MANAGER ====================

class FirebaseManager:
    def __init__(self, service_account_dict: dict, database_url: str):
        try:
            if not firebase_admin._apps:
                cred = credentials.Certificate(service_account_dict)
                firebase_admin.initialize_app(cred, {
                    'databaseURL': database_url
                })
            self.db_ref = db.reference()
        except Exception as e:
            raise Exception(f"Failed to initialize Firebase: {e}")

    def register_user(self, email: str, password: str) -> Dict[str, Any]:
        try:
            user = auth.create_user(email=email, password=password)
            user_ref = self.db_ref.child('users').child(user.uid)
            user_ref.set({
                'email': email,
                'inventory': [],
                'created_at': datetime.now().isoformat()
            })
            return {'success': True, 'uid': user.uid, 'email': user.email}
        except auth.EmailAlreadyExistsError:
            return {'success': False, 'error': 'Email already exists'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def login_user(self, email: str) -> Dict[str, Any]:
        try:
            user = auth.get_user_by_email(email)
            return {'success': True, 'uid': user.uid, 'email': user.email}
        except auth.UserNotFoundError:
            return {'success': False, 'error': 'User not found'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_user_inventory(self, uid: str) -> List[str]:
        try:
            user_ref = self.db_ref.child('users').child(uid).child('inventory')
            inventory = user_ref.get()
            return inventory if inventory else []
        except Exception as e:
            st.error(f"Error retrieving inventory: {e}")
            return []

    def save_user_inventory(self, uid: str, inventory: List[str]) -> bool:
        try:
            user_ref = self.db_ref.child('users').child(uid).child('inventory')
            user_ref.set(inventory)
            return True
        except Exception as e:
            st.error(f"Error saving inventory: {e}")
            return False

    def update_last_login(self, uid: str):
        try:
            user_ref = self.db_ref.child('users').child(uid)
            user_ref.update({'last_login': datetime.now().isoformat()})
        except Exception as e:
            st.error(f"Error updating last login: {e}")

# ==================== EXECUTOR CLASS ====================

class EnhancedCosmeticsExecutor:
    VALID_THEMES = {'cyberpunk', 'dark fantasy', 'coquette', 'old money', 'streetwear'}

    def __init__(self, cosmetics_library: List[Dict[str, Any]],
                 firebase_manager: FirebaseManager,
                 user_id: str,
                 user_email: str,
                 user_password: str,
                 image_base_path: str = ""):
        self.theme: Optional[str] = None
        self.inventory: Set[str] = set()
        self.color_palette: List[str] = []
        self.image_base_path = image_base_path
        self.firebase = firebase_manager
        self.user_id = user_id
        self.user_email = user_email
        self.user_password = user_password

        self.outfits: List[CosmeticOutfit] = []
        for outfit_data in cosmetics_library:
            self.outfits.append(CosmeticOutfit.from_dict(outfit_data))

        firebase_inventory = self.firebase.get_user_inventory(user_id)
        self.inventory = set(item.lower() for item in firebase_inventory)

    def _sync_inventory_to_firebase(self):
        inventory_list = list(self.inventory)
        self.firebase.save_user_inventory(self.user_id, inventory_list)

    def _apply_theme(self, theme: str) -> str:
        theme_lower = theme.lower()
        if theme_lower not in self.VALID_THEMES:
            valid_themes_str = ', '.join(f"'{t}'" for t in sorted(self.VALID_THEMES))
            return f"❌ Error: Invalid theme '{theme}'\nValid themes are: {valid_themes_str}"
        self.theme = theme_lower
        return f"✓ Theme applied: {theme}"

    def _add_item(self, item: str) -> str:
        item_lower = item.lower()
        if item_lower in self.inventory:
            return f"⚠ Item '{item}' is already in inventory"
        self.inventory.add(item_lower)
        self._sync_inventory_to_firebase()
        return f"✓ Added item: {item}"

    def _remove_item(self, item: str) -> str:
        item_lower = item.lower()
        if item_lower not in self.inventory:
            return f"⚠ Item '{item}' not found in inventory"
        self.inventory.remove(item_lower)
        self._sync_inventory_to_firebase()
        return f"✓ Removed item: {item}"

    def _clear_inventory(self, confirm_password: str) -> str:
        if len(self.inventory) == 0:
            return "⚠ Inventory is already empty"
        
        if confirm_password != self.user_password:
            return "❌ Clear inventory cancelled - incorrect password"

        count = len(self.inventory)
        self.inventory.clear()
        self._sync_inventory_to_firebase()
        return f"✓ Inventory cleared ({count} item(s) removed)"

    def _add_item_list(self, items: List[str]) -> str:
        results = []
        added_count = 0
        duplicate_count = 0

        for item in items:
            item_lower = item.lower()
            if item_lower in self.inventory:
                results.append(f"  ⚠ '{item}' already in inventory (skipped)")
                duplicate_count += 1
            else:
                self.inventory.add(item_lower)
                results.append(f"  ✓ '{item}' added")
                added_count += 1

        self._sync_inventory_to_firebase()
        summary = f"Added {added_count} item(s)"
        if duplicate_count > 0:
            summary += f", skipped {duplicate_count} duplicate(s)"
        return summary + "\n" + "\n".join(results)

    def _set_color_palette(self, colors: List[str]) -> str:
        self.color_palette = [color.lower() for color in colors]
        return f"✓ Color palette set: {', '.join(colors)}"

    def _calculate_match_score(self, outfit_items: Set[str],
                               missing_items: Set[str],
                               color_overlap: int,
                               outfit_colors: List[str]) -> float:
        item_score = 0
        if outfit_items:
            items_present = len(outfit_items) - len(missing_items)
            item_score = (items_present / len(outfit_items)) * 60

        color_score = 0
        if self.color_palette and outfit_colors:
            required_colors = max(1, len(outfit_colors))
            color_score = min(color_overlap / required_colors, 1.0) * 40
        elif not self.color_palette:
            color_score = 40

        return item_score + color_score

    def _assemble_cosmetic(self) -> tuple[str, List[CosmeticOutfit]]:
        if not self.theme:
            return "❌ Error: No theme set. Use 'apply theme' first.", []

        if len(self.inventory) == 0:
            return "❌ Error: Inventory is empty. Add items first.", []

        theme_outfits = [o for o in self.outfits if o.theme.lower() == self.theme]

        if not theme_outfits:
            return f"❌ Error: No outfits available for theme '{self.theme}'", []

        matching_outfits = []
        near_matches = []

        for outfit in theme_outfits:
            outfit_items_set = set(item.lower() for item in outfit.items)
            missing_items = outfit_items_set - self.inventory
            has_all_items = len(missing_items) == 0

            color_match = True
            missing_colors = set()
            color_overlap = 0

            if self.color_palette:
                outfit_colors_set = set(color.lower() for color in outfit.colors)
                user_colors_set = set(self.color_palette)
                matching_colors = outfit_colors_set.intersection(user_colors_set)
                missing_colors = outfit_colors_set - user_colors_set
                color_overlap = len(matching_colors)
                color_match = color_overlap >= 1

            match_score = self._calculate_match_score(
                outfit_items_set, missing_items, color_overlap, outfit.colors
            )

            outfit_match = OutfitMatch(
                outfit=outfit,
                missing_items=missing_items,
                missing_colors=missing_colors,
                color_overlap=color_overlap,
                match_score=match_score
            )

            if has_all_items and color_match:
                matching_outfits.append(outfit)
            else:
                near_matches.append(outfit_match)

        if matching_outfits:
            return self._format_matches(matching_outfits), matching_outfits

        return self._provide_detailed_suggestions(near_matches), []

    def _format_matches(self, outfits: List[CosmeticOutfit]) -> str:
        output = [f"✓ Found {len(outfits)} matching outfit(s)!\n"]
        output.append(f"Theme: {self.theme}")

        if self.color_palette:
            output.append(f"Color Palette: {', '.join(self.color_palette)}")

        return "\n".join(output)

    def _provide_detailed_suggestions(self, near_matches: List[OutfitMatch]) -> str:
        if not near_matches:
            return "❌ No matching outfits found."

        near_matches.sort(key=lambda x: x.match_score, reverse=True)
        best_match = near_matches[0]

        output = ["❌ No exact outfit matches found.\n"]
        output.append(f"Current Configuration:")
        output.append(f"  Theme: {self.theme}")
        output.append(f"  Inventory: {', '.join(sorted(self.inventory)) if self.inventory else 'Empty'}")

        if self.color_palette:
            output.append(f"  Color Palette: {', '.join(self.color_palette)}")
        else:
            output.append(f"  Color Palette: Not set")

        output.append("\n" + "="*60)
        output.append("CLOSEST MATCH ANALYSIS")
        output.append("="*60)

        outfit = best_match.outfit
        output.append(f"\nOutfit: {outfit.name}")
        output.append(f"Match Score: {best_match.match_score:.1f}%")
        output.append(f"\nRequired Items: {', '.join(outfit.items)}")
        output.append(f"Required Colors: {', '.join(outfit.colors)}")

        output.append("\n" + "-"*60)
        output.append("DIAGNOSIS:")
        output.append("-"*60)

        if best_match.missing_items:
            output.append(f"\n❌ MISSING ITEMS ({len(best_match.missing_items)}):")
            for item in sorted(best_match.missing_items):
                output.append(f"   • {item}")

        if self.color_palette and best_match.color_overlap < 1:
            output.append(f"\n❌ INSUFFICIENT COLOR MATCHES ({best_match.color_overlap}/1 minimum):")
            if best_match.missing_colors:
                output.append(f"   Missing colors: {', '.join(sorted(best_match.missing_colors))}")

        output.append("\n" + "="*60)
        output.append("SUGGESTIONS TO MATCH THIS OUTFIT:")
        output.append("="*60)

        if best_match.missing_items:
            output.append("\n1. Add missing items:")
            items_str = ' '.join([f"'{item}'" for item in sorted(best_match.missing_items)])
            output.append(f"   {items_str}")

        if self.color_palette and best_match.color_overlap < 1:
            output.append("\n2. Adjust color palette:")
            suggested_colors = list(best_match.missing_colors)[:2]
            if suggested_colors:
                new_colors = suggested_colors + list(self.color_palette)
                colors_str = ' '.join([f"'{c}'" for c in new_colors])
                output.append(f"   >>> color palette {colors_str}")

        output.append("\n" + "="*60)
        return "\n".join(output)

    def execute(self, ast: ASTNode, confirm_password: str = None) -> tuple[str, List[CosmeticOutfit]]:
        command = ast.command
        args = ast.arguments

        if command == 'apply theme':
            return self._apply_theme(args[0]), []
        elif command == 'add item':
            return self._add_item(args[0]), []
        elif command == 'remove item':
            return self._remove_item(args[0]), []
        elif command == 'clear inventory':
            return self._clear_inventory(confirm_password), []
        elif command == 'add item list':
            return self._add_item_list(args), []
        elif command == 'color palette':
            return self._set_color_palette(args), []
        elif command == 'assemble cosmetic':
            return self._assemble_cosmetic()
        else:
            return f"❌ Unknown command: {command}", []

    def get_state(self) -> Dict[str, Any]:
        return {
            'theme': self.theme,
            'inventory': list(self.inventory),
            'color_palette': self.color_palette
        }

# ==================== STREAMLIT APP ====================

def initialize_session_state():
    if 'authenticated' not in st.session_state:
        st.session_state.authenticated = False
    if 'user_id' not in st.session_state:
        st.session_state.user_id = None
    if 'user_email' not in st.session_state:
        st.session_state.user_email = None
    if 'user_password' not in st.session_state:
        st.session_state.user_password = None
    if 'executor' not in st.session_state:
        st.session_state.executor = None
    if 'firebase_manager' not in st.session_state:
        st.session_state.firebase_manager = None
    if 'cosmetics_library' not in st.session_state:
        st.session_state.cosmetics_library = None
    if 'command_history' not in st.session_state:
        st.session_state.command_history = []

def login_page():
    st.markdown('<div class="main-header">👗 Cosmetic Asset Customization</div>', unsafe_allow_html=True)
    
    tab1, tab2 = st.tabs(["Login", "Register"])
    
    with tab1:
        st.subheader("Login to Your Account")
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Password", type="password", key="login_password")
        
        if st.button("Login", type="primary"):
            if email and password:
                result = st.session_state.firebase_manager.login_user(email)
                if result['success']:
                    st.session_state.authenticated = True
                    st.session_state.user_id = result['uid']
                    st.session_state.user_email = email
                    st.session_state.user_password = password
                    st.session_state.firebase_manager.update_last_login(result['uid'])
                    
                    # Initialize executor
                    st.session_state.executor = EnhancedCosmeticsExecutor(
                        st.session_state.cosmetics_library,
                        st.session_state.firebase_manager,
                        result['uid'],
                        email,
                        password,
                        'cosmetics_images'
                    )
                    st.success(f"✓ Login successful! Welcome back, {email}")
                    st.rerun()
                else:
                    st.error(f"❌ Login failed: {result['error']}")
            else:
                st.warning("Please enter both email and password")
    
    with tab2:
        st.subheader("Create New Account")
        email = st.text_input("Email", key="register_email")
        password = st.text_input("Password", type="password", key="register_password")
        confirm_password = st.text_input("Confirm Password", type="password", key="confirm_password")
        
        if st.button("Register", type="primary"):
            if email and password and confirm_password:
                if len(password) < 6:
                    st.error("❌ Password must be at least 6 characters")
                elif password != confirm_password:
                    st.error("❌ Passwords do not match")
                else:
                    result = st.session_state.firebase_manager.register_user(email, password)
                    if result['success']:
                        st.success(f"✓ Registration successful! Welcome, {email}")
                        st.info("Please login with your new account")
                    else:
                        st.error(f"❌ Registration failed: {result['error']}")
            else:
                st.warning("Please fill in all fields")

def main_app():
    # Sidebar
    with st.sidebar:
        st.markdown("### 👤 User Info")
        st.info(f"**Email:** {st.session_state.user_email}")
        
        st.markdown("---")
        st.markdown("### 📊 Current State")
        
        state = st.session_state.executor.get_state()
        st.write(f"**Theme:** {state['theme'] or 'Not set'}")
        st.write(f"**Items in Inventory:** {len(state['inventory'])}")
        if state['inventory']:
            with st.expander("View Inventory"):
                for item in sorted(state['inventory']):
                    st.write(f"• {item}")
        
        if state['color_palette']:
            st.write(f"**Color Palette:** {', '.join(state['color_palette'])}")
        else:
            st.write("**Color Palette:** Not set")
        
        st.markdown("---")
        
        if st.button("🚪 Logout", type="secondary", use_container_width=True):
            st.session_state.authenticated = False
            st.session_state.user_id = None
            st.session_state.user_email = None
            st.session_state.user_password = None
            st.session_state.executor = None
            st.session_state.command_history = []
            st.rerun()
    
    # Main content
    st.markdown('<div class="main-header">👗 Cosmetic Asset Customization</div>', unsafe_allow_html=True)
    
    # Command input section
    st.markdown("### 💬 Command Interface")
    
    col1, col2 = st.columns([3, 1])
    
    with col1:
        command = st.text_input(
            "Enter command:",
            placeholder="e.g., apply theme 'cyberpunk'",
            key="command_input"
        )
    
    with col2:
        st.markdown("<br>", unsafe_allow_html=True)
        execute_button = st.button("▶️ Execute", type="primary", use_container_width=True)
    
    # Command execution
    if execute_button and command:
        try:
            # Check if clear inventory command
            confirm_password = None
            if command.lower().strip() == 'clear inventory':
                confirm_password = st.text_input(
                    "⚠️ Enter password to confirm:",
                    type="password",
                    key="confirm_clear"
                )
                if not confirm_password:
                    st.warning("Please enter your password to confirm clearing inventory")
                    st.stop()
            
            # Parse and execute
            tokenizer = CosmeticsTokenizer(command)
            tokens = tokenizer.tokenize()
            parser = CosmeticsParser(tokens)
            ast = parser.parse()
            
            result, matching_outfits = st.session_state.executor.execute(ast, confirm_password)
            
            # Add to history
            st.session_state.command_history.append({
                'command': command,
                'result': result,
                'timestamp': datetime.now().strftime("%H:%M:%S")
            })
            
            # Display result
            if "✓" in result:
                st.markdown(f'<div class="success-box">{result}</div>', unsafe_allow_html=True)
            elif "❌" in result or "⚠" in result:
                st.markdown(f'<div class="error-box">{result}</div>', unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="info-box">{result}</div>', unsafe_allow_html=True)
            
            # Display matching outfits
            if matching_outfits:
                st.markdown("---")
                st.markdown("### 🎨 Matching Outfits")
                
                for i, outfit in enumerate(matching_outfits, 1):
                    with st.expander(f"**{i}. {outfit.name}**", expanded=True):
                        col1, col2 = st.columns([1, 2])
                        
                        with col1:
                            # Try to display image
                            theme_folder = outfit.theme.replace(" ", "_")
                            img_path = os.path.join('cosmetics_images', theme_folder, outfit.image)
                            
                            if os.path.exists(img_path):
                                st.image(img_path, use_container_width=True)
                            else:
                                st.info("📷 Image not available")
                        
                        with col2:
                            st.markdown("**Items:**")
                            for item in outfit.items:
                                st.write(f"• {item}")
                            
                            st.markdown("**Colors:**")
                            st.write(', '.join(outfit.colors))
                            
                            if outfit.steps:
                                st.markdown("**Assembly Steps:**")
                                for step in outfit.steps:
                                    st.write(f"• {step}")
        
        except ParseError as e:
            st.markdown(f'<div class="error-box">❌ Parse Error: {e}</div>', unsafe_allow_html=True)
        except Exception as e:
            st.markdown(f'<div class="error-box">❌ Error: {e}</div>', unsafe_allow_html=True)
    
    # Help section
    with st.expander("📖 Command Reference"):
        st.markdown("""
        **Available Commands:**
        
        1. `apply theme '<theme_name>'` - Set the current theme
           - Valid themes: cyberpunk, dark fantasy, coquette, old money, streetwear
           - Example: `apply theme 'cyberpunk'`
        
        2. `add item '<item_name>'` - Add a single item to inventory
           - Example: `add item 'jacket'`
        
        3. `add item list '<item1>' '<item2>' ...` - Add multiple items
           - Example: `add item list 'jacket' 'pants' 'hood'`
        
        4. `remove item '<item_name>'` - Remove an item from inventory
           - Example: `remove item 'jacket'`
        
        5. `clear inventory` - Remove all items (requires password confirmation)
        
        6. `color palette '<color1>' '<color2>' ...` - Set color palette
           - Example: `color palette 'neon blue' 'black'`
        
        7. `assemble cosmetic` - Find matching outfits based on current settings
        """)
    
    # Command history
    if st.session_state.command_history:
        st.markdown("---")
        st.markdown("### 📜 Command History")
        
        # Show last 5 commands
        for entry in reversed(st.session_state.command_history[-5:]):
            with st.container():
                st.markdown(f'<div class="command-box">', unsafe_allow_html=True)
                st.markdown(f"**[{entry['timestamp']}]** `{entry['command']}`")
                st.markdown(f'</div>', unsafe_allow_html=True)

def main():
    initialize_session_state()
    
    # Load Firebase configuration
    if st.session_state.firebase_manager is None:
        try:
            # Firebase configuration from JSON
            firebase_config = {
                "type": "service_account",
                "project_id": "cosmetic-c44de",
                "private_key_id": "bb9193b9961826fcbe8038388ff69fafad966965",
                "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDMsaxYAAEDF145\n+5qYMIiFXwd3LyM8EZOg5H4mG6Voh9eFYNFw5EkLiOSkh1MR2+fUJBM2sF75Eg0W\ni0Qs+lenhv6ovnEo+rNy/kNabXh19cqekVrWASxp+mSVgXyQdGDgjNowPlVG3PvV\nkT3TXetuD0IFzXXRdswxHX/QdZXTKWjjK+t2jLyuGohBA0UkA+IhRXGJAfPBUchC\nZ6TjVhASs8v3V7G8uHqlB04hAuK9nFKL7NGSngAbEyCMT1qBETeQGQFB7a0rCzG+\niE7DjZWH6xjLpDJKhTd7Py3IDGWZ83KBJXKfX8EkHvFMcCaYtIRUHHYVp0nq2Ie7\nzWxllYthAgMBAAECggEAB8eWrxVIdJBvPQ1LTM6W6LlB8pFewaQiIBDBG+QL+UUk\n74F8klGl1k+0j22Cfmy1AtzAY3EoeedKCq3eOGanHVOR0xJiYQImvveLkii0Feki\no1xPR1AN+uTorN86D5AxxKULz5613WKtmlpJ3xI8Lf+b1PZALKbrB7CZJJBMgoUI\nX5QqFJVomqmOmoF5NbmVQE5c2SPqaoVEKkV4LtnFPi2AwiMIApn1NKPKwFXRObDJ\n99flkyiWCJrql4pBI/wZiqN7j5YwNJ/ixuQrFegEaRVGYwu5IeHmr/e8mSaqOF8Y\nGJyV1tbtclqoY2KoaOgSBnTARUGbG8WA0ynOZmJoEQKBgQD22ZEBTDOlbngHKrcp\nsWhHikKm5rAr/tgK0TNqISHmofQOxWo+e9ino1zuO/zO9J+6Up2+Jtfy+G8bbKq4\nRKjLAXYPE0h3BM31/jz219HMZI38Deou4oQdCXxdIznnd6UlpHSwIE5prCwVWtQq\nDpuhE40pvoonV04rmiC7iFG1EQKBgQDUSBPHHKxcKQAtliuon+VyGBGYTgcHiIqk\nBSbLyJmbUSh54WqZXSeBfmY83CvfvX/0M+a4TYi2OTmN3g35pKHFdGTCAsj5TGsL\nOXShXfrLXqJyAdm6ROINA2RXuXxSBpnEon/oJyB40rW9REHTlri9YDm2ZK4pcWM0\nW9VR8uMxUQKBgQDwqke0gy2rMVu2aQ/whzWK4iJ/hFPoYOsTCMlexHS/3dALgq9F\nwgsFzcDxx+x/fYIo7xk55bcO/OWeUEDVrKMAYSlQI1W4LCf9mGSpqNqzsqm3P329\nPjzd7nygdZKjuEN7wq29dZHddu331/kYE+vpjB1JwKoDFxxwaDFXhN1ccQKBgCsU\nhY7+7qu1Vmfp9eo+qN3CrK9wBlUtDJXExd7NUv8GNWSmrm95TC8na7AmLnE1j+YL\nMmNsuLXiXx+/VK65Dmt394q37flJ3N9mRZkJ7X+gMO9aGMmIeSeS7KYw6l3rIQGa\nyMJgTmK2wFMsqv85szwbbxroy791V2Ck0mHTHPBBAoGAexctuJSVH8NX8KqzzIJE\n7RcDTZDAHEGZwhaKLFnNbx8DMwl5wyk6PHoHdArUZrX3xxEjzh7JgC08ImGVygNb\neHR4xxEhZI7W6RHt8SxjnbNP9S1Ntgw4YVTXCKboOOkE3yf38pdOH9LHaW4DdzMO\nRhpsTyHvtU4G8y905MSDADQ=\n-----END PRIVATE KEY-----\n",
                "client_email": "firebase-adminsdk-fbsvc@cosmetic-c44de.iam.gserviceaccount.com",
                "client_id": "116634299694524490825",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/firebase-adminsdk-fbsvc%40cosmetic-c44de.iam.gserviceaccount.com",
                "universe_domain": "googleapis.com"
            }
            
            database_url = 'https://cosmetic-c44de-default-rtdb.asia-southeast1.firebasedatabase.app'
            
            st.session_state.firebase_manager = FirebaseManager(firebase_config, database_url)
            
        except Exception as e:
            st.error(f"❌ Firebase initialization failed: {e}")
            st.stop()
    
    # Load cosmetics library
    if st.session_state.cosmetics_library is None:
        try:
            # Embedded cosmetics library
            cosmetics_library = [
                {
                    "name": "Cyberpunk1",
                    "theme": "cyberpunk",
                    "items": ["jacket", "hood", "shades", "pants"],
                    "colors": ["magenta", "neon blue", "black"],
                    "image": "neon_streetwear.jpg",
                    "steps": [
                        "step1: set color of jacket to pink",
                        "step2: set color of shades to black",
                        "step3: set color of pants to brown"
                    ]
                },
                {
                    "name": "darkfantasy1",
                    "theme": "dark fantasy",
                    "items": ["cloak", "hood", "leather pants", "chest armor"],
                    "colors": ["black", "red", "silver"],
                    "image": "raven_witch.jpg",
                    "steps": [
                        "step1: set color of jacket to pink",
                        "step2: set color of shades to black",
                        "step3: set color of pants to brown"
                    ]
                },
                {
                    "name": "coquette1",
                    "theme": "coquette",
                    "items": ["skirt", "sando", "slipper", "jacket"],
                    "colors": ["white", "silver"],
                    "image": "coquette1.jpg",
                    "steps": [
                        "step1: set color of jacket to pink",
                        "step2: set color of shades to black",
                        "step3: set color of pants to brown"
                    ]
                },
                {
                    "name": "oldmoney1",
                    "theme": "old money",
                    "items": ["sando", "sandals", "pants"],
                    "colors": ["white", "brown"],
                    "image": "oldmoney1.jpg",
                    "steps": [
                        "step1: set color of jacket to pink",
                        "step2: set color of shades to black",
                        "step3: set color of pants to brown"
                    ]
                },
                {
                    "name": "streetwear1",
                    "theme": "streetwear",
                    "items": ["jeans", "hoodie", "sneakers"],
                    "colors": ["black", "red", "silver"],
                    "image": "streetwear1.jpg",
                    "steps": [
                        "step1: set color of jacket to pink",
                        "step2: set color of shades to black",
                        "step3: set color of pants to brown"
                    ]
                }
            ]
            
            st.session_state.cosmetics_library = cosmetics_library
            
        except Exception as e:
            st.error(f"❌ Failed to load cosmetics library: {e}")
            st.stop()
    
    # Show appropriate page
    if not st.session_state.authenticated:
        login_page()
    else:
        main_app()

if __name__ == "__main__":
    main()