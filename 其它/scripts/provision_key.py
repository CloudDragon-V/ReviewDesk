"""Read a key via private stdin and save it with Windows Credential Manager."""
import sys
import getpass
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "后端功能"))
from reviewdesk.storage import CredentialStore

key = getpass.getpass("DeepSeek key (hidden): ").strip()
CredentialStore().set_key(key)
key = ""
print("Credential saved to Windows Credential Manager; no plaintext file created.")
