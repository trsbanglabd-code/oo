import asyncio
import io
import time
import re
import json
import html
import os

# ==================== v16.16 ZERO-DEP BOOTSTRAP ====================
# Pure-stdlib fallbacks: when telegram / httpx / pyotp are NOT installed (e.g. a hosting
# panel with no pip), embedded shims are written to a temp dir and put on sys.path —
# this single code file then runs with ZERO package downloads.
_ZD_PYOTP_SRC = r'''"""Zero-dep pyotp shim (v16.16) — RFC 6238 TOTP in pure stdlib (hmac/hashlib/base64)."""
import base64
import hashlib
import hmac
import struct
import time


class TOTP:
    def __init__(self, secret, digits=6, interval=30):
        self.secret = str(secret or "")
        self.digits = int(digits)
        self.interval = int(interval)

    def _key(self):
        s = self.secret.strip().replace(" ", "").upper()
        s += "=" * (-len(s) % 8)
        return base64.b32decode(s)

    def at(self, for_time):
        counter = int(for_time // self.interval)
        hs = hmac.new(self._key(), struct.pack(">Q", counter), hashlib.sha1).digest()
        o = hs[-1] & 0x0F
        code = (struct.unpack(">I", hs[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** self.digits)
        return str(code).zfill(self.digits)

    def now(self):
        return self.at(time.time())
'''
_ZD_HTTPX_SRC = r'''"""Zero-dep httpx shim (v16.16) — minimal AsyncClient over stdlib urllib (via threads)."""
import asyncio
import functools
import json as _json
import urllib.error
import urllib.parse
import urllib.request


async def _thread(fn, *args):
    """Run a blocking call in the default executor (works on Python 3.7+, no to_thread)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, functools.partial(fn, *args))


class Timeout:
    def __init__(self, connect=5.0, read=5.0, write=5.0, pool=5.0):
        self.connect = connect
        self.read = read
        self.write = write
        self.pool = pool

    def __float__(self):
        return float(max(self.connect + self.read + self.write + self.pool, 1.0))


class Limits:
    def __init__(self, **kw):
        pass


class Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)

    def json(self):
        return _json.loads(self.text)


class AsyncClient:
    def __init__(self, timeout=None, headers=None, limits=None, params=None, follow_redirects=False, **kw):
        self.timeout = timeout if timeout is not None else Timeout()
        self.headers = dict(headers or {})
        self.params = dict(params or {})
        self.follow_redirects = follow_redirects

    async def aclose(self):
        return None

    def _request(self, method, url, params=None, json=None):
        query = dict(self.params)
        if params:
            query.update(params)
        if query:
            sep = "&" if "?" in url else "?"
            url = url + sep + urllib.parse.urlencode(query)
        data = None
        headers = dict(self.headers)
        if json is not None:
            data = _json.dumps(json).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=float(self.timeout)) as r:
                return Response(r.status, r.read())
        except urllib.error.HTTPError as e:
            return Response(e.code, e.read())

    async def get(self, url, params=None, **kw):
        return await _thread(self._request, "GET", url, params, None)

    async def post(self, url, params=None, json=None, **kw):
        return await _thread(self._request, "POST", url, params, json)
'''
_ZD_TG_SRC = r'''"""Zero-dep telegram shim (v16.16) — data classes + update/message objects.
Networking lives in telegram.ext (Bot._do_post is the single funnel)."""
from .error import TelegramError, BadRequest, RetryAfter, Conflict, TimedOut, NetworkError, Forbidden


class User:
    def __init__(self, id, first_name=None, last_name=None, username=None, is_bot=False, language_code=None, **kw):
        self.id = id
        self.first_name = first_name or ""
        self.last_name = last_name or ""
        self.username = username
        self.is_bot = is_bot
        self.language_code = language_code

    @property
    def full_name(self):
        return " ".join(x for x in (self.first_name, self.last_name) if x) or (self.username or str(self.id))

    @classmethod
    def _from_json(cls, d):
        if not d:
            return None
        return cls(d.get("id"), d.get("first_name"), d.get("last_name"), d.get("username"),
                   d.get("is_bot", False), d.get("language_code"))

    def __repr__(self):
        return f"User(id={self.id})"


class Chat:
    def __init__(self, id, type="private", title=None, username=None, **kw):
        self.id = id
        self.type = type
        self.title = title
        self.username = username

    @classmethod
    def _from_json(cls, d):
        if not d:
            return None
        return cls(d.get("id"), d.get("type", "private"), d.get("title"), d.get("username"))


class MessageEntity:
    CUSTOM_EMOJI = "custom_emoji"
    TEXT_MENTION = "text_mention"
    TEXT_LINK = "text_link"
    BOLD = "bold"
    ITALIC = "italic"
    CODE = "code"
    PRE = "pre"

    def __init__(self, type, offset, length, custom_emoji_id=None, **kw):
        self.type = type
        self.offset = offset
        self.length = length
        self.custom_emoji_id = custom_emoji_id

    @classmethod
    def _from_json(cls, d):
        if not d:
            return None
        return cls(d.get("type"), d.get("offset", 0), d.get("length", 0), d.get("custom_emoji_id"))


class _File:
    def __init__(self, file_id, file_unique_id=None, width=None, height=None, file_size=None,
                 duration=None, file_name=None, emoji=None, custom_emoji_id=None, **kw):
        self.file_id = file_id
        self.file_unique_id = file_unique_id
        self.width = width
        self.height = height
        self.file_size = file_size
        self.duration = duration
        self.file_name = file_name
        self.emoji = emoji
        self.custom_emoji_id = custom_emoji_id

    @classmethod
    def _from_json(cls, d):
        if not d:
            return None
        return cls(d.get("file_id"), d.get("file_unique_id"), d.get("width"), d.get("height"),
                   d.get("file_size"), d.get("duration"), d.get("file_name"),
                   d.get("emoji"), d.get("custom_emoji_id"))


PhotoSize = _File
Document = _File
Video = _File
Audio = _File
Voice = _File
Animation = _File
Sticker = _File


class ChatMember:
    def __init__(self, status=None, user=None, **kw):
        self.status = status
        self.user = user

    @classmethod
    def _from_json(cls, d):
        if not d:
            return None
        return cls(d.get("status"), User._from_json(d.get("user")))


class Message:
    def __init__(self, message_id=0, chat=None, from_user=None, text=None, caption=None,
                 entities=None, caption_entities=None, photo=None, document=None, video=None,
                 audio=None, voice=None, animation=None, sticker=None, date=None, bot=None, **kw):
        self.message_id = message_id
        self.chat = chat
        self.from_user = from_user
        self.text = text
        self.caption = caption
        self.entities = entities or []
        self.caption_entities = caption_entities or []
        self.photo = photo or []
        self.document = document
        self.video = video
        self.audio = audio
        self.voice = voice
        self.animation = animation
        self.sticker = sticker
        self.date = date
        self._bot = bot

    @property
    def chat_id(self):
        return self.chat.id if self.chat else None

    async def reply_text(self, text, **kw):
        return await self._bot.send_message(self.chat_id, text, **kw)

    async def edit_text(self, text, **kw):
        return await self._bot.edit_message_text(chat_id=self.chat_id, message_id=self.message_id, text=text, **kw)

    async def delete(self, **kw):
        return await self._bot.delete_message(self.chat_id, self.message_id)

    def parse_entities(self, types=None):
        return [e for e in self.entities if not types or e.type in types]

    def parse_caption_entities(self, types=None):
        return [e for e in self.caption_entities if not types or e.type in types]

    @classmethod
    def _from_json(cls, d, bot=None):
        if not d:
            return None
        return cls(
            message_id=d.get("message_id", 0),
            chat=Chat._from_json(d.get("chat")),
            from_user=User._from_json(d.get("from")),
            text=d.get("text"),
            caption=d.get("caption"),
            entities=[MessageEntity._from_json(e) for e in d.get("entities") or []],
            caption_entities=[MessageEntity._from_json(e) for e in d.get("caption_entities") or []],
            photo=[_File._from_json(p) for p in d.get("photo") or []],
            document=_File._from_json(d.get("document")),
            video=_File._from_json(d.get("video")),
            audio=_File._from_json(d.get("audio")),
            voice=_File._from_json(d.get("voice")),
            animation=_File._from_json(d.get("animation")),
            sticker=_File._from_json(d.get("sticker")),
            date=d.get("date"),
            bot=bot,
        )


class CallbackQuery:
    def __init__(self, id="", from_user=None, message=None, data=None, bot=None, **kw):
        self.id = id
        self.from_user = from_user
        self.message = message
        self.data = data
        self._bot = bot

    async def answer(self, text=None, show_alert=False, **kw):
        return await self._bot.answer_callback_query(self.id, text=text, show_alert=show_alert)

    def get_bot(self):
        return self._bot

    @classmethod
    def _from_json(cls, d, bot=None):
        if not d:
            return None
        return cls(d.get("id", ""), User._from_json(d.get("from")),
                   Message._from_json(d.get("message"), bot), d.get("data"), bot)


class Update:
    ALL_TYPES = ["message", "edited_message", "channel_post", "edited_channel_post",
                 "callback_query", "my_chat_member", "chat_member", "inline_query",
                 "chosen_inline_result", "poll", "poll_answer"]

    def __init__(self, update_id=0, message=None, callback_query=None, edited_message=None, **kw):
        self.update_id = update_id
        self.message = message
        self.callback_query = callback_query
        self.edited_message = edited_message

    @property
    def effective_user(self):
        if self.message is not None:
            return self.message.from_user
        if self.callback_query is not None:
            return self.callback_query.from_user
        return None

    @property
    def effective_chat(self):
        if self.message is not None:
            return self.message.chat
        if self.callback_query is not None and self.callback_query.message is not None:
            return self.callback_query.message.chat
        return None

    @property
    def effective_message(self):
        if self.message is not None:
            return self.message
        if self.callback_query is not None:
            return self.callback_query.message
        return None


class KeyboardButton:
    def __init__(self, text, style=None, **kw):
        self.text = text
        self.style = style

    def to_dict(self):
        d = {"text": self.text}
        if self.style:
            d["style"] = self.style
        return d


class InlineKeyboardButton:
    def __init__(self, text, callback_data=None, url=None, style=None, copy_text=None, **kw):
        self.text = text
        self.callback_data = callback_data
        self.url = url
        self.style = style
        self.copy_text = copy_text

    def to_dict(self):
        d = {"text": self.text}
        if self.callback_data is not None:
            d["callback_data"] = self.callback_data
        if self.url is not None:
            d["url"] = self.url
        if self.style:
            d["style"] = self.style
        if self.copy_text is not None:
            d["copy_text"] = self.copy_text.to_dict() if hasattr(self.copy_text, "to_dict") else self.copy_text
        return d


class ReplyKeyboardMarkup:
    def __init__(self, keyboard, resize_keyboard=None, is_persistent=None,
                 input_field_placeholder=None, **kw):
        self.keyboard = keyboard
        self.resize_keyboard = resize_keyboard
        self.is_persistent = is_persistent
        self.input_field_placeholder = input_field_placeholder

    def to_dict(self):
        d = {"keyboard": [[b.to_dict() for b in row] for row in self.keyboard]}
        if self.resize_keyboard is not None:
            d["resize_keyboard"] = bool(self.resize_keyboard)
        if self.is_persistent is not None:
            d["is_persistent"] = bool(self.is_persistent)
        if self.input_field_placeholder:
            d["input_field_placeholder"] = self.input_field_placeholder
        return d


class InlineKeyboardMarkup:
    def __init__(self, inline_keyboard, **kw):
        self.inline_keyboard = inline_keyboard

    def to_dict(self):
        return {"inline_keyboard": [[b.to_dict() for b in row] for row in self.inline_keyboard]}


class BotCommand:
    def __init__(self, command, description):
        self.command = command
        self.description = description

    def to_dict(self):
        return {"command": self.command, "description": self.description}


class CopyTextButton:
    def __init__(self, text):
        self.text = text

    def to_dict(self):
        return {"text": self.text}


class ReactionTypeEmoji:
    def __init__(self, emoji):
        self.emoji = emoji

    def to_dict(self):
        return {"type": "emoji", "emoji": self.emoji}


def update_from_json(d, bot=None):
    msg = d.get("message") or d.get("edited_message")
    cb = d.get("callback_query")
    return Update(
        update_id=d.get("update_id", 0),
        message=Message._from_json(msg, bot) if msg else None,
        edited_message=Message._from_json(d.get("edited_message"), bot) if d.get("edited_message") else None,
        callback_query=CallbackQuery._from_json(cb, bot) if cb else None,
    )
'''
_ZD_TG_ERR_SRC = r'''"""Zero-dep telegram.error shim (v16.16)."""


class TelegramError(Exception):
    def __init__(self, message=""):
        super().__init__(message)
        self.message = message


class BadRequest(TelegramError):
    pass


class Forbidden(TelegramError):
    pass


class Conflict(TelegramError):
    pass


class TimedOut(TelegramError):
    pass


class NetworkError(TelegramError):
    pass


class RetryAfter(TelegramError):
    def __init__(self, message="", retry_after=5):
        super().__init__(message)
        self.retry_after = retry_after
'''
_ZD_TG_EXT_SRC = r'''"""Zero-dep telegram.ext shim (v16.16): raw Bot API client + long polling + dispatch."""
import asyncio
import functools
import json
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid


async def _thread(fn, *args):
    """Run a blocking call in the default executor (works on Python 3.7+, no to_thread)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, functools.partial(fn, *args))

from . import Update, Message, User, ChatMember, Sticker, update_from_json
from .error import TelegramError, BadRequest, RetryAfter, Conflict, TimedOut, NetworkError, Forbidden


class Bot:
    """Raw Bot API client. _do_post is the SINGLE network funnel — PremiumBot overrides it."""

    def __init__(self, token=None, **kw):
        self.token = token
        self._base = "https://api.telegram.org/bot" + str(token)
        self._me = None

    # ---------- network funnel ----------
    async def _do_post(self, endpoint, data, **kwargs):
        fields, files = [], []
        for k, v in (data or {}).items():
            if v is None:
                continue
            if hasattr(v, "to_dict"):
                v = v.to_dict()
            if isinstance(v, (dict, list)):
                v = json.dumps(v)
            if hasattr(v, "read"):
                files.append((k, v))
            else:
                fields.append((k, v))
        body, ctype = self._encode(fields, files)
        raw = await _thread(self._http, f"{self._base}/{endpoint}", body, ctype)
        d = json.loads(raw)
        if not d.get("ok"):
            desc = str(d.get("description", "unknown error"))
            code = d.get("error_code", 0)
            params = d.get("parameters") or {}
            if code == 429:
                raise RetryAfter(desc, retry_after=params.get("retry_after", 5))
            if code == 409:
                raise Conflict(desc)
            if code == 400:
                raise BadRequest(desc)
            if code == 403:
                raise Forbidden(desc)
            if code in (500, 502, 503, 504):
                raise TimedOut(desc)
            raise TelegramError(desc)
        return d.get("result")

    @staticmethod
    def _encode(fields, files):
        if not files:
            return urllib.parse.urlencode(fields).encode("utf-8"), "application/x-www-form-urlencoded"
        boundary = "----zd" + uuid.uuid4().hex
        chunks = []
        for k, v in fields:
            chunks.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
        for k, fh in files:
            fname = str(getattr(fh, "name", "file") or "file").split("/")[-1]
            payload = fh.read()
            chunks.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fname}\"\r\n"
                           f"Content-Type: application/octet-stream\r\n\r\n").encode("utf-8") + payload + b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
        return b"".join(chunks), f"multipart/form-data; boundary={boundary}"

    @staticmethod
    def _http(url, body, ctype):
        req = urllib.request.Request(url, data=body, headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            return e.read()

    # ---------- convenience API ----------
    @staticmethod
    def _clean(kw):
        return {k: v for k, v in kw.items() if v is not None}

    async def send_message(self, chat_id, text, **kw):
        r = await self._do_post("sendMessage", {"chat_id": chat_id, "text": text, **self._clean(kw)})
        return Message._from_json(r, self) if isinstance(r, dict) else r

    async def edit_message_text(self, text, chat_id=None, message_id=None, **kw):
        r = await self._do_post("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text, **self._clean(kw)})
        return Message._from_json(r, self) if isinstance(r, dict) else r

    async def delete_message(self, chat_id, message_id, **kw):
        return await self._do_post("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    async def _send_media(self, endpoint, chat_id, media, **kw):
        r = await self._do_post(endpoint, {"chat_id": chat_id, endpoint[4:].lower(): media, **self._clean(kw)})
        return Message._from_json(r, self) if isinstance(r, dict) else r

    async def send_photo(self, chat_id, photo, **kw):
        return await self._send_media("sendPhoto", chat_id, photo, **kw)

    async def send_document(self, chat_id, document, **kw):
        return await self._send_media("sendDocument", chat_id, document, **kw)

    async def send_video(self, chat_id, video, **kw):
        return await self._send_media("sendVideo", chat_id, video, **kw)

    async def send_voice(self, chat_id, voice, **kw):
        return await self._send_media("sendVoice", chat_id, voice, **kw)

    async def send_audio(self, chat_id, audio, **kw):
        return await self._send_media("sendAudio", chat_id, audio, **kw)

    async def send_animation(self, chat_id, animation, **kw):
        return await self._send_media("sendAnimation", chat_id, animation, **kw)

    async def send_sticker(self, chat_id, sticker, **kw):
        return await self._send_media("sendSticker", chat_id, sticker, **kw)

    async def set_message_reaction(self, chat_id, message_id, reaction=None, is_big=None, **kw):
        return await self._do_post("setMessageReaction", {"chat_id": chat_id, "message_id": message_id,
                                                         "reaction": reaction, "is_big": is_big})

    async def send_chat_action(self, chat_id, action, **kw):
        return await self._do_post("sendChatAction", {"chat_id": chat_id, "action": action})

    async def get_me(self):
        if self._me is None:
            self._me = User._from_json(await self._do_post("getMe", {}))
        return self._me

    async def set_my_commands(self, commands, **kw):
        return await self._do_post("setMyCommands", {"commands": [c.to_dict() if hasattr(c, "to_dict") else c for c in commands]})

    async def set_my_description(self, description, **kw):
        return await self._do_post("setMyDescription", {"description": description})

    async def set_my_short_description(self, short_description, **kw):
        return await self._do_post("setMyShortDescription", {"short_description": short_description})

    async def answer_callback_query(self, callback_query_id, text=None, show_alert=None, **kw):
        return await self._do_post("answerCallbackQuery", {"callback_query_id": callback_query_id,
                                                           "text": text, "show_alert": show_alert})

    async def get_chat_member(self, chat_id, user_id, **kw):
        return ChatMember._from_json(await self._do_post("getChatMember", {"chat_id": chat_id, "user_id": user_id}))

    async def get_custom_emoji_stickers(self, custom_emoji_ids, **kw):
        return [Sticker._from_json(s) for s in await self._do_post("getCustomEmojiStickers", {"custom_emoji_ids": custom_emoji_ids})]

    async def set_chat_photo(self, chat_id, photo, **kw):
        return await self._do_post("setChatPhoto", {"chat_id": chat_id, "photo": photo})

    async def copy_message(self, chat_id, from_chat_id, message_id, **kw):
        r = await self._do_post("copyMessage", {"chat_id": chat_id, "from_chat_id": from_chat_id,
                                                "message_id": message_id, **self._clean(kw)})
        mid = r.get("message_id") if isinstance(r, dict) else r
        return type("MessageId", (), {"message_id": mid})()


ExtBot = Bot


# ---------- filters ----------
class _Filter:
    def __init__(self, fn):
        self.fn = fn

    def check(self, update):
        try:
            return bool(self.fn(update))
        except Exception:
            return False

    def __and__(self, other):
        return _Filter(lambda u: self.check(u) and other.check(u))

    def __or__(self, other):
        return _Filter(lambda u: self.check(u) or other.check(u))

    def __invert__(self):
        return _Filter(lambda u: not self.check(u))


def _msg(update):
    return update.message


class filters:
    TEXT = _Filter(lambda u: bool(getattr(_msg(u), "text", None)))
    COMMAND = _Filter(lambda u: str(getattr(_msg(u), "text", "") or "").startswith("/"))
    PHOTO = _Filter(lambda u: bool(getattr(_msg(u), "photo", None)))
    VIDEO = _Filter(lambda u: bool(getattr(_msg(u), "video", None)))
    AUDIO = _Filter(lambda u: bool(getattr(_msg(u), "audio", None)))
    VOICE = _Filter(lambda u: bool(getattr(_msg(u), "voice", None)))
    ANIMATION = _Filter(lambda u: bool(getattr(_msg(u), "animation", None)))

    class ChatType:
        GROUPS = _Filter(lambda u: getattr(getattr(_msg(u), "chat", None), "type", "") in ("group", "supergroup"))
        PRIVATE = _Filter(lambda u: getattr(getattr(_msg(u), "chat", None), "type", "") == "private")

    class Sticker:
        ALL = _Filter(lambda u: bool(getattr(_msg(u), "sticker", None)))

    class Document:
        ALL = _Filter(lambda u: bool(getattr(_msg(u), "document", None)))


# ---------- handlers / context / application ----------
class CommandHandler:
    def __init__(self, command, callback, **kw):
        self.command = command
        self.callback = callback

    def check(self, update):
        m = update.message
        if not m or not m.text or not m.text.startswith("/"):
            return False
        cmd = m.text.split()[0][1:].split("@")[0].lower()
        return cmd == str(self.command).lower()


class MessageHandler:
    def __init__(self, filters, callback, **kw):
        self.filters = filters
        self.callback = callback

    def check(self, update):
        f = self.filters
        return f.check(update) if hasattr(f, "check") else True


class CallbackQueryHandler:
    def __init__(self, callback, **kw):
        self.callback = callback

    def check(self, update):
        return update.callback_query is not None


class _Context:
    def __init__(self, application, update):
        self.application = application
        self.bot = application.bot
        u = update.effective_user
        self.user_data = application._user_data.setdefault(u.id if u else 0, {})
        self.args = []
        m = update.message
        if m and m.text and m.text.startswith("/"):
            self.args = m.text.split()[1:]


class ContextTypes:
    DEFAULT_TYPE = _Context


class Application:
    def __init__(self, bot, post_init=None):
        self.bot = bot
        self._post_init = post_init
        self.handlers = []
        self._user_data = {}
        self._offset = 0
        self._tasks = set()

    def add_handler(self, handler):
        self.handlers.append(handler)

    async def _dispatch(self, update):
        for h in self.handlers:
            try:
                if h.check(update):
                    await h.callback(update, _Context(self, update))
                    return
            except Exception:
                traceback.print_exc()

    async def _poll_once(self):
        result = await self.bot._do_post("getUpdates", {"offset": self._offset, "timeout": 30,
                                                        "allowed_updates": Update.ALL_TYPES})
        for d in result or []:
            self._offset = d.get("update_id", self._offset) + 1
            task = asyncio.create_task(self._dispatch(update_from_json(d, self.bot)))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run(self, drop_pending_updates=False):
        try:
            await self.bot._do_post("deleteWebhook", {"drop_pending_updates": bool(drop_pending_updates)})
        except Exception:
            pass
        if drop_pending_updates:
            try:
                r = await self.bot._do_post("getUpdates", {"offset": -1, "timeout": 0, "limit": 1})
                if r:
                    self._offset = r[-1].get("update_id", 0) + 1
            except Exception:
                pass
        if self._post_init:
            await self._post_init(self)
        while True:
            try:
                await self._poll_once()
            except Conflict as e:
                print(f"telegram.error.Conflict: {e}")
                raise SystemExit(1)
            except (NetworkError, TimedOut) as e:
                print(f"[polling] {type(e).__name__}: {e} — retrying in 3s")
                await asyncio.sleep(3)
            except Exception:
                traceback.print_exc()
                await asyncio.sleep(3)

    def run_polling(self, **kw):
        asyncio.run(self._run(drop_pending_updates=kw.get("drop_pending_updates", False)))


class ApplicationBuilder:
    def __init__(self):
        self._bot = None
        self._post_init = None

    def bot(self, bot):
        self._bot = bot
        return self

    def concurrent_updates(self, v):
        return self

    def post_init(self, cb):
        self._post_init = cb
        return self

    def build(self):
        if self._bot is None:
            raise RuntimeError("ApplicationBuilder.bot(...) is required")
        return Application(self._bot, self._post_init)
'''

import importlib as _importlib
import sys as _sys
import tempfile as _tempfile


def _zd_ensure(modname, files):
    """Import modname; if missing, materialise the embedded shim into a temp dir on sys.path."""
    try:
        _importlib.import_module(modname)
        return
    except Exception:
        pass
    root = None
    for base in (_tempfile.gettempdir(), os.path.dirname(os.path.abspath(__file__))):
        try:
            root = os.path.join(base, "alifotp_zero_dep")
            for rel, src in files.items():
                path = os.path.join(root, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as _f:
                    _f.write(src)
            break
        except Exception:
            root = None
    if root:
        if root not in _sys.path:
            _sys.path.insert(0, root)
        print(f"[zero-dep] {modname} stdlib shim active — no pip install needed")


_zd_ensure("pyotp", {"pyotp.py": _ZD_PYOTP_SRC})
_zd_ensure("httpx", {"httpx.py": _ZD_HTTPX_SRC})
_zd_ensure("telegram", {"telegram/__init__.py": _ZD_TG_SRC,
                         "telegram/error.py": _ZD_TG_ERR_SRC,
                         "telegram/ext.py": _ZD_TG_EXT_SRC})
# ==================== END ZERO-DEP BOOTSTRAP ====================


import httpx
import pyotp
import random
import string
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, MessageEntity
from telegram.error import BadRequest, RetryAfter
try:
    from telegram import CopyTextButton
except ImportError:            # older PTB
    CopyTextButton = None
try:
    from telegram import ReactionTypeEmoji
except ImportError:
    ReactionTypeEmoji = None
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler, ExtBot

# ==================== CONFIG SECTION ====================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8847418350:AAF0HPfB6YCnNkR7YgHf26ND1jE-KvcdF_8")
# ───────────────────────────── NUMBER PANEL: YESMS ─────────────────────────────
# Single provider: YEHUB / yesms.online  (FastAPI panel)
#   GET  /api/live_access_data      → {"services":[...], "ranges": {"Facebook": ["🔥 🇸🇩 Sudan | 249X1", ...]}}
#   POST /api/allocate_number       → body {"range_id": "22879XXX"} → {"success": true, "data": {"full_number": "+228…"}}
#   GET  /api/success_logs          → {"logs": [{"number","range","service","message","code","time"}]}
#   GET  /api/user_numbers          → {"logs": [{"number","otp_code","full_message","time"}]}
#   Auth header:  AuthKey: <api key>
YESMS_BASE_URL = os.environ.get("YESMS_BASE_URL", "https://yesms.online").rstrip("/")
YESMS_API_KEY  = os.environ.get("YESMS_API_KEY", "4f8d9048a8027c42")

# legacy aliases (older code paths / admin views)
BASE_URL = YESMS_BASE_URL
API_KEY  = YESMS_API_KEY

# ───────────────────────────── NUMBER PANEL #2: STEXSMS (tness · 2oo9 CLOUD) ─────────────────────────────
#   Dashboard: https://stexsms.com/m29/#/auth/        Auth header:  mauthapi: <api key>
#   POST {base}/getnum       body {"rid": "22870"}  (range digits, no XXX) → data{full_number, no_plus_number, country, operator}
#   GET  {base}/liveaccess   → data.services[{sid, ranges:["22879XXX",…], last_at}]
#   GET  {base}/console      → data.hits[{range, sid, message, time(ms)}]   (global traffic, last 15 min)
#   GET  {base}/success-otp  → data.otps[{otp_id, number, message, sid, time(ms)}]   (my last 50 OTPs)
TNESS_BASE_URL = os.environ.get("TNESS_BASE_URL", "https://api.2oo9.cloud/MXS47FLFX0U/tness/@public/api").rstrip("/")
TNESS_API_KEY  = os.environ.get("TNESS_API_KEY", "M1M2XLU5VSK")

PANELS = [
    {"id": "yesms", "name": "YESMS", "base_url": YESMS_BASE_URL, "api_key": YESMS_API_KEY, "auth_header": "AuthKey", "kind": "yesms"},
    {"id": "tness", "name": "STEXSMS", "base_url": TNESS_BASE_URL, "api_key": TNESS_API_KEY, "auth_header": "mauthapi", "kind": "2oo9"},
]
USER_DATA_FILE = "users.json"
PAID_SMS_FILE = "paid_sms.json"
STATS_FILE = "user_stats.json"
REFERRAL_DATA_FILE = "referral_data.json"
BANNED_USERS_FILE = "banned_users.json"
WITHDRAW_DATA_FILE = "withdraw_requests.json"
ACTIVITY_LOGS_FILE = "activity_logs.json"
DATA_RANGE_FILE = "datarange.json"
SYS_CONFIG_FILE = "sys_config.json"
ADMINS_FILE = "admins.json"
WELCOME_VIDEOS_FILE = "welcome_videos.json"
HIDDEN_SERVICES_FILE = "hidden_services.json"
SETTINGS_FILE = "bot_settings.json"
PREMIUM_EMOJI_FILE = "premium_emoji.json"

# ==================== HIDDEN / SPONSOR CHANNEL CONFIG ====================
def _get_h_lnk():
    # Obfuscated URL composition to protect the link from basic extraction
    p1 = "ht" + "tp" + "s://"
    p2 = "t." + "me" + "/x"
    p3 = "de" + "ve" + "lo" + "pe"
    p4 = "r1" + "2"
    return p1 + p2 + p3 + p4

# ==================== ADMIN CONFIGURATION ====================
BASE_ADMINS = [7450863375,8172034235]          # Super admins — full power incl. admin team control
OTP_GROUP_ID =  -1003718683357
RANGE_GROUP_LINK = "https://t.me/+kQTYy3ObNCtmNDc1"   # v16.12: range announcements live here

def range_group_id():
    """v16.15: chat id of the RANGE group (set via /setrangegroup sent INSIDE the group).
    Returns None when unset — range posts are then DROPPED (never leaked into the OTP group)."""
    try:
        cfg = load_sys_config() or {}
        rid = cfg.get("range_group_id")
        return int(rid) if rid else None
    except Exception:
        return None

def panel_rates():
    """v16.15: per-panel taka-per-OTP rates {panel_id: rate} (sys_config 'panel_rates')."""
    cfg = load_sys_config() or {}
    pr = cfg.get("panel_rates")
    return dict(pr) if isinstance(pr, dict) else {}

def panel_rate(pid):
    """v16.15: taka rewarded per OTP delivered by this panel (admin-tunable; default OTP_RATE)."""
    try:
        return float(panel_rates().get(str(pid), OTP_RATE))
    except Exception:
        return OTP_RATE

def set_panel_rate(pid, rate):
    """v16.15: set a panel's taka-per-OTP rate (persisted in sys_config)."""
    cfg = load_sys_config() or {}
    pr = cfg.get("panel_rates") if isinstance(cfg.get("panel_rates"), dict) else {}
    pr[str(pid)] = round(max(0.0, float(rate)), 4)
    cfg["panel_rates"] = pr
    save_sys_config(cfg)
OTP_GROUP_JOIN_LINK = "https://t.me/numbergenfastbot"   # Public OTP group join link
OTP_GROUP_TITLE = "OTP GROUP"
PARTNER_TITLE = "PARTNER ZONE"
DEFAULT_CHANNEL_TITLE = "OFFICIAL ZONE"
# ==================== OTP RATE & VALUATION ====================
OTP_RATE = 0.30
REFERRAL_PRICE = 5
MIN_WITHDRAW = 700
MAX_WITHDRAW = 10000
MULTI_MAX = 10  # Maximum numbers per burst allocation
MAX_WELCOME_VIDEOS = 10

# ==================== SUPPORT & DEVELOPER LINKS ====================
SUPPORT_LINK = "https://t.me/alifshahadat"
DEVELOPER_LINK = "https://t.me/rsrownok"
SUPPORT_HANDLE = "@alifshahadat"

# ==================== PREMIUM DESIGN SYSTEM ====================
# Custom unicode typography: converts plain text into styled glyphs so
# Telegram buttons & headers render with a "custom font" premium look.

_BOLD_MAP = {}
for _i, _c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    _BOLD_MAP[_c] = chr(0x1D400 + _i)
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    _BOLD_MAP[_c] = chr(0x1D41A + _i)
for _i, _c in enumerate("0123456789"):
    _BOLD_MAP[_c] = chr(0x1D7CE + _i)

def ub(text):
    """Unicode Bold — for cinematic card headers."""
    return "".join(_BOLD_MAP.get(ch, ch) for ch in str(text))

_SMALL_MAP = {
    'a': 'ᴀ', 'b': 'ʙ', 'c': 'ᴄ', 'd': 'ᴅ', 'e': 'ᴇ', 'f': 'ꜰ', 'g': 'ɢ',
    'h': 'ʜ', 'i': 'ɪ', 'j': 'ᴊ', 'k': 'ᴋ', 'l': 'ʟ', 'm': 'ᴍ', 'n': 'ɴ',
    'o': 'ᴏ', 'p': 'ᴘ', 'q': 'ǫ', 'r': 'ʀ', 's': 'ꜱ', 't': 'ᴛ', 'u': 'ᴜ',
    'v': 'ᴠ', 'w': 'ᴡ', 'x': 'x', 'y': 'ʏ', 'z': 'ᴢ',
}

def sc(text):
    """Small-Caps — premium button typography."""
    return "".join(_SMALL_MAP.get(ch.lower(), ch) for ch in str(text))

DIV = "━━━━━━━━━━━━━━━━━━━━"
DIV_S = "━━━━━━━━━━━━━━━━━━"
BRAND_NAME = "TOP OTP ELITE"
BRAND_LINE = f"👑 {BRAND_NAME}"
BRAND_SHORT = f"👑 {BRAND_NAME}"
SUPPORT_NAME = "Alif Shahadat"          # shown on the SUPPORT card (edit freely)

def head(title, icon="👑"):
    """Card header — screenshot style:
       ━━━━━━━━━━━━━━━━━━━━
       👑 TITLE
       ━━━━━━━━━━━━━━━━━━━━"""
    return f"{DIV}\n{icon} <b>{html.escape(str(title)).upper()}</b>\n{DIV}\n\n"

def boxed(title, icon="👤"):
    """Double-line framed title (used on MY PROFILE, like the screenshot)."""
    return f"╔{'═' * 20}╗\n║  {icon} <b>{html.escape(str(title)).upper()}</b>\n╚{'═' * 20}╝\n\n"

_NUM_BADGES = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

def num_badge(i):
    return _NUM_BADGES[i - 1] if 1 <= i <= 10 else f"{i}."

# ==================== PERSISTENT CONFIGURATION LOADER ====================
def load_sys_config():
    if not os.path.exists(SYS_CONFIG_FILE):
        default_config = {
            "official_channel_id": -1003298796462,
            "official_channel_link": "https://t.me/+wP66wN0oXT8xMzY9"
        }
        with open(SYS_CONFIG_FILE, "w") as f:
            json.dump(default_config, f, indent=4)
        return default_config
    try:
        with open(SYS_CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"official_channel_id": -1003298796462, "official_channel_link": "https://t.me/+wP66wN0oXT8xMzY9"}

def save_sys_config(cfg):
    with open(SYS_CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=4)

# Load global configuration
sys_cfg = load_sys_config()

# ==================== PREMIUM BUTTON FACTORY ====================
# Every button = [lead icon] + [typography] + [optional accent icon]
# Lead icon = the "logo" of the action, accent = subtle premium sparkle.

def pbtn(icon, text, accent="", caps=False):
    """Button label = logo-icon + plain text (screenshot style). `accent` kept for API compat (unused)."""
    label = sc(text) if caps else text
    return f"{icon} {label}"

# ==================== BUTTON LABELS (SINGLE SOURCE OF TRUTH) ====================
BTN = {
    "en": {
        # ── main reply keyboard (exactly like the reference UI) ──
        "get_num": "📞 GET NUMBER",
        "traffic": "📊 TRAFFIC",
        "profile": "👤 MY PROFILE",
        "support": "👥 SUPPORT",
        # ── v16.10 custom square menu (reference screenshot) ──
        "get_number_v2": "📱 Get Number",
        "view_range": "📊 View Range",
        "my_status": "📈 My Status",
        "withdraw_v2": "🤑 Withdraw",
        "refer_earn": "🎁 Refer & Earn",
        "support_admin": "👤 Support Admin",
        "switch_v2": "🔄 Switch V2",
        # ── reached via /commands or inline buttons ──
        "multi": "🎰 MULTI NUMBERS",
        "search": "🔍 SEARCH OTP",
        "2fa": "🔐 GET 2FA",
        "balance": "💳 BALANCE",
        "refer": "🎁 REFER LINK",
        "leaderboard": "🏆 LEADERBOARD",
        "lang": "🌐 LANGUAGE",
        "admin": "🛡️ ADMIN PANEL",
        "cancel": "❌ Cancel",
        "join": "📢 Join Channel",
        "otp_group": "📢 Join Group",
        "partner": "📢 Join Channel",
        "verify": "✅ Check Joined",
        "withdraw": "💳 WITHDRAW",
        "custom_range": "🛠 CUSTOM RANGE",
        "back": "◀️ BACK",
        "repeat": "🔁 REPEAT",
        "otp_feed": "📣 OTP FEED",
        "ref_report": "📊 MY REF LOG",
        "abort": "❌ Abort",
        "confirm": "✅ CONFIRM",
        "close": "❌ Close",
        "refresh": "⚙️ Refresh",
        "stock": "📁 STOCK",
    },
    "bn": {
        "traffic": "📊 ট্রাফিক",
        "close": "❌ বন্ধ করুন",
        "refresh": "⚙️ রিফ্রেশ",
        "stock": "📁 স্টক",
        "get_num": pbtn("💎", "নাম্বার নিন", "⚡", caps=False),
        "multi": pbtn("🎰", "মাল্টি নাম্বার", "🔥", caps=False),
        "search": pbtn("🔎", "ওটিপি খুঁজুন", caps=False),
        "2fa": pbtn("🔐", "2FA কোড", caps=False),
        "balance": pbtn("💰", "ব্যালেন্স", caps=False),
        "refer": pbtn("🎁", "রেফার ও ইনকাম", caps=False),
        "profile": pbtn("👑", "প্রোফাইল", caps=False),
        "leaderboard": pbtn("🏆", "লিডারবোর্ড", caps=False),
        "support": pbtn("🎧", "সাপোর্ট", caps=False),
        # ── v16.10 custom square menu (reference screenshot) ──
        "get_number_v2": "📱 নাম্বার নিন",
        "view_range": "📊 রেঞ্জ দেখুন",
        "my_status": "📈 আমার স্ট্যাটাস",
        "withdraw_v2": "🤑 উইথড্র",
        "refer_earn": "🎁 রেফার করুন",
        "support_admin": "👤 সাপোর্টแอดมิน",
        "switch_v2": "🔄 V2 এ যান",
        "lang": pbtn("🌐", "ভাষা পরিবর্তন", caps=False),
        "admin": pbtn("🛡️", "অ্যাডমিন প্যানেল", "👑", caps=False),
        "cancel": pbtn("❌", "বাতিল", caps=False),
        "join": pbtn("📢", "চ্যানেলে জয়েন", "🔥", caps=False),
        "otp_group": pbtn("✈️", "ওটিপি গ্রুপ", "📡", caps=False),
        "partner": pbtn("🤝", "পার্টনার জোন", "💎", caps=False),
        "verify": pbtn("✅", "ভেরিফাই করুন", "✨", caps=False),
        "withdraw": pbtn("💸", "উইথড্র করুন", "🏦", caps=False),
        "custom_range": pbtn("🧩", "কাস্টম রেঞ্জ", caps=False),
        "back": pbtn("↩️", "ফিরে যান", caps=False),
        "repeat": pbtn("🔁", "আবার নিন", "⚡", caps=False),
        "otp_feed": pbtn("📡", "ওটিপি ফিড", caps=False),
        "ref_report": pbtn("📊", "রেফার রিপোর্ট", caps=False),
        "abort": pbtn("❌", "বাতিল", caps=False),
        "confirm": pbtn("✅", "কনফার্ম", "✨", caps=False),
    }
}

# Legacy labels (previous build) — kept so users holding the OLD reply keyboard
# still get routed correctly until they tap /start and receive the new one.
_LEGACY_BTN = {
    "get_num": [f"💠 {sc('GET NUMBER')}", "💠 নাম্বার নিন", f"💎 {sc('GET NUMBER')} ⚡", f"{sc('GET NUMBER')} ⚡"],
    "traffic": [],
    "multi": [f"🎰 {sc('MULTI NUMBERS')}", "🎰 মাল্টি নাম্বার"],
    "search": [f"🔍 {sc('SEARCH OTP')}", "🔍 ওটিপি খুঁজুন"],
    "2fa": [f"🔐 {sc('GET 2FA')}", "🔐 2FA কোড"],
    "balance": [f"💰 {sc('BALANCE')}", "💰 ব্যালেন্স"],
    "refer": [f"🎁 {sc('REFER & EARN')}", "🎁 রেফার ও ইনকাম"],
    "profile": [f"👑 {sc('PROFILE')}", "👑 প্রোফাইল", sc('PROFILE')],
    "leaderboard": [f"🏆 {sc('LEADERBOARD')}", "🏆 লিডারবোর্ড"],
    "support": [f"🎧 {sc('SUPPORT')}", "🎧 সাপোর্ট", sc('SUPPORT')],
    "lang": [f"🌐 {sc('LANGUAGE')}", "🌐 ভাষা পরিবর্তন"],
    "admin": [f"🛡️ {sc('ADMIN PANEL')}", "🛡️ অ্যাডমিন প্যানেল"],
    "cancel": [f"🚫 {sc('CANCEL')}", "🚫 বাতিল", f"🚫 {sc('ABORT')}"],
}

def _labels(key):
    """All accepted labels for a button key: current (en+bn) + legacy."""
    return [BTN["en"][key], BTN["bn"][key]] + _LEGACY_BTN.get(key, [])

# ==================== ADMIN PANEL LABELS (SINGLE SOURCE OF TRUTH) ====================
A_USER_CENTER = pbtn("👥", "USER CONTROL")
A_SYSTEM_CORE = pbtn("⚙️", "SYSTEM CORE")
A_ADMIN_TEAM = pbtn("🛡️", "ADMIN TEAM")
A_EXIT = pbtn("🏠", "EXIT PANEL")
A_BROADCAST = pbtn("📡", "BROADCAST ALL")
A_IDS = pbtn("🗂️", "ALL USER IDS")
A_BALANCES = pbtn("💎", "ALL BALANCES")
A_BANLIST = pbtn("🚫", "BAN LIST")
A_STATS = pbtn("📊", "DAILY STATS")
A_INSPECT = pbtn("🕵️", "INSPECT USER")
A_PAYOUTS = pbtn("📥", "PENDING PAYS")
A_VIDEOS = pbtn("🎬", "SET VIDEOS")
A_CLEAR_VIDEOS = pbtn("🗑️", "CLEAR VIDEOS")
A_SERVICES = pbtn("🎛️", "SERVICE CTRL")
A_NUMPICK = pbtn("🔢", "NUM PER PICK")
A_ADD_ADMIN = pbtn("➕", "ADD ADMIN")
A_REMOVE_ADMIN = pbtn("➖", "REMOVE ADMIN")
A_ADMIN_LIST = pbtn("📋", "ADMIN LIST")
A_BAN = pbtn("⛔", "BAN USER")
A_UNBAN = pbtn("🔓", "UNBAN USER")
A_INJECT = pbtn("💎", "INJECT BAL")
A_PURGE = pbtn("💸", "PURGE BAL")
A_UPDATE_JOIN = pbtn("📣", "UPDATE JOIN")
A_EMOJI = pbtn("🎨", "PREMIUM EMOJI")
A_PANELS = pbtn("🛰️", "NUMBER PANELS")
A_PANEL_RATES = pbtn("💵", "PANEL RATES")   # v16.15: per-panel taka/OTP rate
A_BACK = pbtn("↩️", "BACK")
A_DONE = pbtn("✅", "DONE")

# ==================== WITHDRAW GATEWAY LABELS (BRAND-COLOURED LOGOS) ====================
W_BKASH = pbtn("💗", "BKASH")        # bKash pink
W_NAGAD = pbtn("🧡", "NAGAD")        # Nagad orange
W_ROCKET = pbtn("💜", "ROCKET")      # Rocket purple
W_BINANCE = pbtn("🟡", "BINANCE")    # Binance gold
WITHDRAW_METHOD_MAP = {
    W_BKASH: "BKASH", W_NAGAD: "NAGAD", W_ROCKET: "ROCKET", W_BINANCE: "BINANCE",
    # legacy labels (old keyboards still open on some phones)
    f"💖 {sc('BKASH')}": "BKASH", f"🧡 {sc('NAGAD')}": "NAGAD",
    f"🚀 {sc('ROCKET')}": "ROCKET", f"🏦 {sc('BINANCE')}": "BINANCE",
}

# ==================== UI COPY (SMALL PROMPTS / NOTIFICATIONS) ====================
LANG_TEXTS = {
    "en": {
        "banned": f"{head('ACCESS DENIED', '🚫')}<b>Your account has been suspended by the admin.</b>\n<i>Contact support if you believe this is a mistake.</i>",
        "withdraw_min_err": f"{head('LOW BALANCE', '📉')}💳 <b>Balance:</b> {{bal}} ৳\n🛡️ <b>Minimum:</b> {{min_val}} ৳",
        "withdraw_amount_prompt": f"{head('ENTER AMOUNT', '💳')}Type the amount to withdraw <i>(Min: {{min_val}} ৳)</i>:",
        "withdraw_number_prompt": f"{head('PAYMENT NUMBER', '📲')}Send your receiving wallet number\nFormat: <code>017XXXXXXXX</code>",
        "search_otp_prompt": f"{head('SEARCH OTP', '🔍')}<b>Enter the number to search its OTP:</b>",
        "search_otp_searching": "🔍 <b>Searching, please wait...</b>",
        "get_2fa_prompt": f"{head('2FA CODE', '🔐')}<b>Send your 2FA secret key:</b>",
        "2fa_invalid": f"{head('INVALID KEY', '🚫')}The secret key you sent is invalid. Check & retry.",
        "custom_range_prompt": f"{head('CUSTOM RANGE', '🛠')}Type a custom range\nExample: <code>234XXX</code>",
        "invalid_range": f"{head('BAD RANGE', '🚫')}Valid format example: <code>234XXX</code>",
        "withdraw_method_prompt": f"{head('PAYMENT METHOD', '💳')}Select your payment method 👇",
    },
    "bn": {
        "banned": f"🚫 『 {ub('ACCESS DENIED')} 』 🚫\n{DIV}\n\n<b>অ্যাডমিন কোর আপনার নোডটি সাসপেন্ড করেছে।</b>\n<i>ভুল হয়ে থাকলে সাপোর্টে যোগাযোগ করুন।</i>",
        "withdraw_min_err": f"📉 『 {ub('LOW BALANCE')} 』\n\n<blockquote>💰 ব্যালেন্স   ▸ <code>{{bal}} BDT</code>\n📉 সর্বনিম্ন   ▸ <code>{{min_val}} BDT</code></blockquote>",
        "withdraw_amount_prompt": f"💰 『 {ub('ENTER AMOUNT')} 』\n{DIV}\n\nউইথড্র করার পরিমাণটি লিখুন <i>(সর্বনিম্ন: {{min_val}} BDT)</i>:",
        "withdraw_number_prompt": f"📲 『 {ub('DESTINATION')} 』\n{DIV}\n\nপেমেন্ট নম্বরটি পাঠান\nফরম্যাট: <code>017XXXXXXXX</code>",
        "search_otp_prompt": f"🔍 『 {ub('OTP SEARCH')} 』\n{DIV}\n\n<b>ওটিপি খুঁজতে টার্গেট নম্বরটি দিন:</b>",
        "search_otp_searching": "🔍 <b>ডেটাবেজ প্যাকেট স্ক্যান হচ্ছে, অপেক্ষা করুন...</b>",
        "get_2fa_prompt": f"🔐 『 {ub('2FA GENERATOR')} 』\n{DIV}\n\n<b>আপনার 2FA সিক্রেট কী-টি পাঠান:</b>",
        "2fa_invalid": f"🚫 『 {ub('INVALID KEY')} 』\n\nআপনার পাঠানো সিক্রেট কী-টি সঠিক নয়। পুনরায় ট্রাই করুন।",
        "custom_range_prompt": f"🛠 『 {ub('CUSTOM RANGE')} 』\n{DIV}\n\nম্যানুয়ালি রেঞ্জ ইনপুট করুন\nউদাহরণ: <code>234XXX</code>",
        "invalid_range": f"🚫 『 {ub('BAD RANGE')} 』\n\nসঠিক ফরম্যাটের উদাহরণ: <code>234XXX</code>",
        "withdraw_method_prompt": f"💳 『 {ub('PICK GATEWAY')} 』\n{DIV}\n\nপেমেন্ট গেটওয়ে সিলেক্ট করুন 👇",
    }
}

# ==================== BUTTON MATCHERS (ROUTING) ====================
# Current + legacy labels are both accepted → zero-downtime keyboard upgrade.
T_GET_NUM = _labels("get_num") + [BTN["en"]["get_number_v2"], BTN["bn"]["get_number_v2"]]
T_TRAFFIC = _labels("traffic") + [BTN["en"]["view_range"], BTN["bn"]["view_range"]]
T_MULTI = _labels("multi")
T_SEARCH_OTP = _labels("search")
T_2FA = _labels("2fa")
T_BALANCE = _labels("balance") + [BTN["en"]["withdraw_v2"], BTN["bn"]["withdraw_v2"]]
T_REFER = _labels("refer") + [BTN["en"]["refer_earn"], BTN["bn"]["refer_earn"]]
T_PROFILE = _labels("profile") + [BTN["en"]["my_status"], BTN["bn"]["my_status"]]
T_LEADERBOARD = _labels("leaderboard")
T_SUPPORT = _labels("support") + [BTN["en"]["support_admin"], BTN["bn"]["support_admin"]]
T_LANG = _labels("lang")
T_ADMIN = _labels("admin")
# v16.10 square menu — the new custom buttons route here too
T_SWITCH_V2 = [BTN["en"]["switch_v2"], BTN["bn"]["switch_v2"], "Switch V2", "switch v2"]
T_CANCEL = _labels("cancel") + [BTN["en"]["abort"], BTN["bn"]["abort"], "🛑 CANCEL", "🛑 বাতিল", "🛑 ABORT"]

# v16.5: every main-keyboard button press sweeps a 🔥 across the screen (fire on tap)
MENU_BUTTON_TEXTS = (set(T_GET_NUM) | set(T_TRAFFIC) | set(T_MULTI) | set(T_SEARCH_OTP) | set(T_2FA)
                     | set(T_BALANCE) | set(T_REFER) | set(T_PROFILE) | set(T_LEADERBOARD)
                     | set(T_SUPPORT) | set(T_LANG) | set(T_CANCEL))

request_queue = None  # created inside post_init (needs the running event loop)

def _panel_ready(p):
    return bool(p.get("api_key")) and bool(p.get("base_url")) and "HERE" not in p.get("base_url", "")

def _mk_client(p):
    hdr = p.get("auth_header") or "X-API-Key"
    val = p.get("api_key", "")
    if p.get("bearer") or (hdr.lower() == "authorization" and not str(val).lower().startswith("bearer ")):
        val = f"Bearer {val}"
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=15.0, write=5.0, pool=5.0),
        headers={hdr: val},
        limits=httpx.Limits(max_connections=1000, max_keepalive_connections=200)
    )

async def _fetch_retry(fn, attempts=3, backoff=1.5):
    """v16.6: call fn() up to `attempts` times with growing backoff — survives slow routes,
    brief panel hiccups and rate-limit windows on the host server."""
    last = None
    for i in range(attempts):
        try:
            return await fn()
        except Exception as e:
            last = e
            if i < attempts - 1:
                await asyncio.sleep(backoff * (i + 1))
    raise last

# ==================== DYNAMIC PANELS (v12) ====================
# Admin adds ANY yehub-style OTP panel from Telegram:  /addpanel NAME https://panel.tld APIKEY
# The bot probes the auth header itself, saves it to panels.json and merges the panel into
# GET NUMBER (services/countries/ranges), the OTP monitor and the 🔥 traffic scanner.
PANELS_FILE = "panels.json"
AUTH_HEADER_CANDIDATES = ["AuthKey", "X-API-Key", "Authorization", "api-key", "apikey", "X-Auth-Key", "Api-Key", "token", "key"]

def _load_dynamic_panels():
    try:
        if os.path.exists(PANELS_FILE):
            with open(PANELS_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return [x for x in d if isinstance(x, dict) and x.get("id") and x.get("base_url")] if isinstance(d, list) else []
    except Exception as e:
        print(f"[panels] load fail: {e}")
    return []

def _save_dynamic_panels():
    try:
        dyn = [{k: p.get(k) for k in ("id", "name", "base_url", "api_key", "auth_header", "bearer", "kind")} for p in PANELS if p.get("dynamic")]
        with open(PANELS_FILE, "w", encoding="utf-8") as f:
            json.dump(dyn, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[panels] save fail: {e}")

for _dp in _load_dynamic_panels():
    if not any(p["id"] == _dp["id"] or p["base_url"] == _dp["base_url"] for p in PANELS):
        _dp["dynamic"] = True
        PANELS.append(_dp)

for _p in PANELS:
    _p["client"] = _mk_client(_p)
    _p["enabled"] = _panel_ready(_p)
    _p["status"] = {"online": None, "services": 0, "last_ok": None, "error": ""}

client_async = PANELS[0]["client"]          # legacy alias (YESMS)

def active_panels():
    return [p for p in PANELS if p.get("enabled")]

def panel_by_id(pid):
    for p in PANELS:
        if p["id"] == pid:
            return p
    return PANELS[0]

_RANGE_PANEL = {}       # range_text → panel id (filled on every liveaccess refresh)

def panel_for_range(range_text):
    return panel_by_id(_RANGE_PANEL.get(str(range_text), PANELS[0]["id"]))

# ==================== 2oo9 CLOUD ADAPTER (v13) ====================
def _is_2oo9(p):
    return p.get("kind") == "2oo9" or "/@public/api" in str(p.get("base_url", "")) or "2oo9" in str(p.get("base_url", ""))

async def _2oo9_get(p, path):
    """GET a 2oo9 public endpoint → data dict (raises on error envelope)."""
    r = await p["client"].get(f"{p['base_url']}/{path}")
    d = r.json()
    if not isinstance(d, dict) or (d.get("meta") or {}).get("code") != 200:
        raise RuntimeError((d or {}).get("message", f"HTTP {r.status_code}") if isinstance(d, dict) else f"HTTP {r.status_code}")
    return d.get("data") or {}

def _good_sid(sid):
    sid = str(sid or "").strip()
    return bool(sid) and not re.fullmatch(r"[+\d\s]+", sid)        # drop phone-number "senders"

async def _2oo9_otps(p):
    try:
        d = await _2oo9_get(p, "success-otp")
    except Exception as e:
        if str(e):
            print(f"[{p['id']}] success-otp error: {e}")
        return []
    out = []
    for o in d.get("otps") or []:
        if not isinstance(o, dict) or not o.get("number") or not o.get("message"):
            continue
        num = re.sub(r"[^0-9]", "", str(o["number"]))
        own = active_numbers.get(normalize_number(num)) or {}
        out.append({
            "number": num, "message": o["message"], "otp": None,
            "otp_id": f"{p['id']}_{o.get('otp_id') or num + '_' + str(o.get('time'))}",
            "range": own.get("range", ""), "service": o.get("sid", ""),
            "panel": p["id"], "ts": _yesms_time(o),
        })
    return out

def _yesms_otp_row(o, pid):
    """Normalise a yesms log row → the shape monitor_loop/search expect."""
    number = str(o.get("number") or "")
    msg = o.get("message") or o.get("full_message") or ""
    code = str(o.get("code") or o.get("otp_code") or "")
    if not number or not msg:
        return None
    return {
        "number": number,
        "message": msg,
        "otp": code or None,
        "otp_id": f"{re.sub(r'[^0-9]', '', number)}_{code or abs(hash(msg))}",
        "range": o.get("range", ""),
        "service": o.get("service", ""),
        "panel": pid,
    }

def _yesms_time(o):
    """unix seconds from a yesms row ('time' = unix int, or '2026-09-24 02:10:10 PM'); None if unknown."""
    t = o.get("time")
    try:
        if isinstance(t, (int, float)):
            return float(t) / (1000.0 if t > 1e12 else 1.0)
        if isinstance(t, str) and t.strip():
            for fmt in ("%Y-%m-%d %I:%M:%S %p", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(t.strip(), fmt).timestamp()
                except ValueError:
                    pass
    except Exception:
        pass
    return None

async def _yesms_feed(p, path, params=None):
    try:
        r = await p["client"].get(f"{p['base_url']}{path}", params=params)
        res = r.json()
        if isinstance(res, dict) and res.get("error"):
            print(f"[{p['id']}] {path}: {res.get('error')}")
            return []
        return (res.get("logs") or []) if isinstance(res, dict) else []
    except Exception as e:
        if str(e):
            print(f"[{p['id']}] {path} error: {e}")
        return []

async def panel_success_otps():
    """Every OTP yesms knows for this account right now — BOTH feeds, every cycle, de-duplicated.
    (success_logs = today's hits with range/service; user_numbers = my numbers with their codes)"""
    out, seen = [], set()
    for p in active_panels():
        if _is_2oo9(p):
            for n in await _2oo9_otps(p):
                if n["otp_id"] not in seen:
                    seen.add(n["otp_id"]); out.append(n)
            continue
        a, b = await asyncio.gather(
            _yesms_feed(p, "/api/success_logs", {"day_offset": 0, "page": 1}),
            _yesms_feed(p, "/api/user_numbers"),
        )
        for o in list(a) + list(b):
            n = _yesms_otp_row(o, p["id"]) if isinstance(o, dict) else None
            if n and n["otp_id"] not in seen:
                n["ts"] = _yesms_time(o)
                seen.add(n["otp_id"]); out.append(n)
                if n.get("range") and n.get("service"):
                    auto_upsert(n["service"], n["range"], source="otp", ts=n["ts"], touch=True)
    return out

active_numbers = {}
last_range = {}
CHECK_INTERVAL = 2.0

_liveaccess_cache = {"services": []}
LIVEACCESS_REFRESH_INTERVAL = 25

_RANGE_META = {}   # range_id → {"flag": "🇹🇬", "country": "Togo", "hot": bool}

def _parse_yesms_range(label):
    """'🔥 🇸🇩 Sudan | 249X1'  →  ('249X1', {'flag':'🇸🇩','country':'Sudan','hot':True})"""
    raw = str(label).replace("\u200d", "").strip()
    hot = "🔥" in raw
    if "|" in raw:
        left, rid = raw.rsplit("|", 1)
    else:
        left, rid = "", raw
    rid = re.sub(r"[^0-9A-Za-z]", "", rid).upper()
    left = left.replace("🔥", "").strip()
    flag = "".join(c for c in left if 0x1F1E6 <= ord(c) <= 0x1F1FF)
    country = "".join(c for c in left if not (0x1F1E6 <= ord(c) <= 0x1F1FF)).strip()
    return rid, {"flag": flag, "country": country, "hot": hot}

async def _panel_liveaccess(p):
    """yesms /api/live_access_data → [{"sid": name, "ranges": [range_id, ...]}] (live services only)."""
    if _is_2oo9(p):
        try:
            d = await _fetch_retry(lambda: _2oo9_get(p, "liveaccess"), attempts=3)
            svcs = []
            for x in d.get("services") or []:
                if not isinstance(x, dict) or not _good_sid(x.get("sid")):
                    continue
                rids = []
                for rg in x.get("ranges") or []:
                    rid = _norm_rid(rg)
                    if re.match(r"^\d{2,}X", rid) and rid not in rids:
                        rids.append(rid)
                        f, c = get_country_info(_rid_prefix(rid))
                        _RANGE_META.setdefault(rid, {"flag": f, "country": c, "hot": False})
                if rids:
                    svcs.append({"sid": str(x["sid"]).strip(), "ranges": rids})
            p["status"].update({"online": True, "services": len(svcs), "last_ok": datetime.now(), "error": ""})
            return svcs
        except Exception as e:
            p["status"].update({"online": False, "error": str(e)[:80]})
            print(f"[{p['id']}] liveaccess error: {e}")
            return []
    try:
        r = await _fetch_retry(lambda: p["client"].get(f"{p['base_url']}/api/live_access_data"), attempts=3)
        data = r.json()
        if not isinstance(data, dict) or data.get("error"):
            raise RuntimeError((data or {}).get("error", "bad response") if isinstance(data, dict) else "bad response")
        svcs, meta = [], {}
        for name, items in (data.get("ranges") or {}).items():
            rids = []
            for lab in items or []:
                rid, m = _parse_yesms_range(lab)
                if rid and rid not in rids:
                    rids.append(rid); meta[rid] = m
            if rids:
                svcs.append({"sid": str(name), "ranges": rids})
        _RANGE_META.update(meta)
        p["status"].update({"online": True, "services": len(svcs), "last_ok": datetime.now(), "error": ""})
        return svcs
    except Exception as e:
        p["status"].update({"online": False, "error": str(e)[:80]})
        print(f"[{p['id']}] live_access error: {e}")
        return []

async def _do_liveaccess_fetch():
    """Fetch every enabled panel in parallel and MERGE them into one service list.
    Same service name on two panels → ranges are combined; every range remembers its panel."""
    global _liveaccess_cache
    panels = active_panels()
    results = await asyncio.gather(*(_panel_liveaccess(p) for p in panels))
    merged, order = {}, []
    range_owner = {}
    for p, svcs in zip(panels, results):
        for svc in svcs or []:
            if not isinstance(svc, dict):
                continue
            sid = str(svc.get("sid") or svc.get("name") or svc.get("service") or "Service")
            key = sid.strip().lower()
            if key not in merged:
                merged[key] = {"sid": sid, "ranges": [], "panels": []}
                order.append(key)
            m = merged[key]
            if p["id"] not in m["panels"]:
                m["panels"].append(p["id"])
            for rg in svc.get("ranges", []) or []:
                rg = str(rg)
                if rg not in m["ranges"]:
                    m["ranges"].append(rg)
                range_owner.setdefault(rg, p["id"])      # first panel that lists a range owns it
    svcs_out = [merged[k] for k in order]
    global _LIVE_RAW, _LIVE_PAIRS
    if svcs_out:
        _LIVE_RAW = svcs_out
        _LIVE_PAIRS = {f"{_svc_key(s['sid'])}|{r}" for s in svcs_out for r in s["ranges"]}
        _RANGE_PANEL.clear(); _RANGE_PANEL.update(range_owner)
        for s in svcs_out:                       # AUTO: every live pair goes into the catalogue too
            for r in s["ranges"]:
                mm = _RANGE_META.get(r, {})
                auto_upsert(s["sid"], r, mm.get("flag", ""), mm.get("country", ""), hot=mm.get("hot"), source="live", panel=_RANGE_PANEL.get(r))
    out = _rebuild_services_cache()
    if svcs_out:
        print(f"[panels] cache updated — {len(out)} service(s) ({len(svcs_out)} live + auto) from {', '.join(p['name'] for p in panels)}")

async def liveaccess_refresh_loop():
    while True:
        _LOOP_TICK["live"] = time.time()
        await _do_liveaccess_fetch()
        await asyncio.sleep(LIVEACCESS_REFRESH_INTERVAL)

def get_cached_services():
    return _liveaccess_cache["services"]

# ==================== AUTO DISCOVERY ENGINE (v11) ====================
# Nothing is added by hand any more. Services, countries and ranges are
# discovered automatically from THREE live sources and merged into the
# catalogue that 📞 GET NUMBER is built from:
#   1. /api/live_access_data  → official service → range list            (source "live")
#   2. /api/console_data      → the global OTP firehose: every row carries
#                               range + flag/country + service, so a brand-new
#                               range / country / service shows up in the menu
#                               within seconds                               (source "traffic")
#   3. our own OTP feed       → ranges that actually paid us                 (source "otp")
# Admins may still pin a range by hand (/addrange) — pinned ranges never expire.
AUTO_CATALOG_FILE = "auto_catalog.json"
AUTO_DISCOVERY_INTERVAL = 15         # seconds between console scans (v16.6: gentler on panel rate-limits)
AUTO_RANGE_TTL = 6 * 3600            # traffic-discovered range disappears after 6 h of silence
AUTO_ANNOUNCE = True                 # post 🆕 AUTO RANGE ADDED cards to OTP_GROUP_ID
AUTO_HOT_HITS = 1                    # any OTP traffic in the last hour → 🔥 badge

_LIVE_RAW = []                       # last raw service list from live_access_data
_LIVE_PAIRS = set()                  # "svc_key|RID" currently listed by live_access
_AUTO = {"pairs": {}, "names": {}}   # pairs: "svc_key|RID" → meta ; names: svc_key → display name
_AUTO_DIRTY = False
_AUTO_STATE = {"console_ok": None, "last_scan": None, "rows": 0, "new_total": 0, "booted": False, "scans": 0}
_TRAFFIC_SEEN = {}                   # dedupe key → (ts, "🇨🇲 Cameroon", service, rid)   (rolling 24 h)

def _svc_key(name):
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()

def _norm_rid(rid):
    return re.sub(r"[^0-9A-Za-z]", "", str(rid or "").replace("\u200d", "")).upper()

def _load_auto_catalog():
    global _AUTO
    try:
        if os.path.exists(AUTO_CATALOG_FILE):
            with open(AUTO_CATALOG_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("pairs"), dict):
                _AUTO = {"pairs": d["pairs"], "names": d.get("names", {})}
                print(f"[auto] catalog loaded — {len(_AUTO['pairs'])} service/range pair(s)")
    except Exception as e:
        print(f"[auto] catalog load fail: {e}")

def _save_auto_catalog():
    global _AUTO_DIRTY
    try:
        with open(AUTO_CATALOG_FILE, "w", encoding="utf-8") as f:
            json.dump(_AUTO, f, ensure_ascii=False)
        _AUTO_DIRTY = False
    except Exception as e:
        print(f"[auto] catalog save fail: {e}")

def _split_country(label):
    """'🇸🇩 Sudan' → ('🇸🇩', 'Sudan')"""
    raw = str(label or "").replace("\u200d", "").strip()
    flag = "".join(c for c in raw if 0x1F1E6 <= ord(c) <= 0x1F1FF)
    country = "".join(c for c in raw if not (0x1F1E6 <= ord(c) <= 0x1F1FF)).strip()
    return flag, country

def _rid_prefix(rid):
    return re.sub(r"\D", "", re.sub(r"[xX].*$", "", str(rid or "")))

def _learn_country(rid, flag, country):
    """AUTO COUNTRY — teach COUNTRY_MAP a dial-prefix it does not know yet."""
    if not (flag and country):
        return False
    prefix = _rid_prefix(rid)
    if len(prefix) < 2 or prefix in COUNTRY_MAP:
        return False
    f, c = get_country_info(prefix)
    if c == "Unknown" or f != flag:
        COUNTRY_MAP[prefix] = (flag, country)
        _SORTED_PREFIXES[:] = sorted(COUNTRY_MAP.keys(), key=len, reverse=True)
        print(f"[auto] +country {flag} {country} (prefix {prefix})")
        return True
    return False

def auto_upsert(service, rid, flag="", country="", hot=None, source="traffic", ts=None, hit=False, touch=False, panel=None):
    """AUTO SERVICE + AUTO RANGE — register a (service, range) pair. Returns True when it is brand new."""
    global _AUTO_DIRTY
    service = re.sub(r"\s+", " ", str(service or "")).strip()
    rid = _norm_rid(rid)
    if not service or service.lower() in ("unknown", "none", "sms service") or not re.match(r"^\d{2,}X", rid):
        return False
    key = _svc_key(service)
    now = float(ts) if ts else time.time()
    _AUTO["names"].setdefault(key, service)
    pk = f"{key}|{rid}"
    m = _AUTO["pairs"].get(pk)
    new = m is None
    if new:
        m = {"service": service, "rid": rid, "flag": "", "country": "", "hot": False, "hits": [],
             "first_seen": now, "last_seen": now, "last_hit": 0, "source": source, "pinned": False}
        _AUTO["pairs"][pk] = m
    if flag:
        m["flag"] = flag
    if country:
        m["country"] = country
    if hot is not None:
        m["hot"] = bool(hot)
    if source == "manual":
        m["pinned"] = True
    if source == "live":
        m["source"] = "live"
    if panel:
        m["panel"] = panel
    m["last_seen"] = max(float(m.get("last_seen") or 0), now)
    if hit:
        hits = m.setdefault("hits", [])
        if now not in hits:
            hits.append(now)
            del hits[:-300]
    if hit or touch:
        m["last_hit"] = max(float(m.get("last_hit") or 0), now)
    if m.get("flag") and m.get("country"):
        _learn_country(rid, m["flag"], m["country"])
    _AUTO_DIRTY = True
    return new

def _range_hits(m, window=3600):
    now = time.time()
    return sum(1 for t in (m.get("hits") or []) if t > now - window)

def _rebuild_services_cache():
    """Merge live_access + discovered catalogue → _liveaccess_cache['services'] (+ _RANGE_META)."""
    now = time.time()
    # v16.7 outage guard: when NO panel is reachable, keep last-known-good pairs (skip the 6h TTL)
    # so the service picker never falls into MAINTENANCE during a total panel outage.
    panels_up = any((p.get("status") or {}).get("online") for p in active_panels())
    meta = {k: dict(v) for k, v in _RANGE_META.items()}
    for mm in meta.values():
        mm["hits"] = 0
    merged, order = {}, []
    for svc in _LIVE_RAW:                                   # 1) official live services
        key = _svc_key(svc["sid"])
        merged[key] = {"sid": svc["sid"], "ranges": list(svc["ranges"]), "panels": list(svc.get("panels", [])),
                       "src": {r: "live" for r in svc["ranges"]}}
        order.append(key)
    expired = []
    for pk, m in list(_AUTO["pairs"].items()):              # 2) discovered / pinned pairs
        key, rid = pk.split("|", 1)
        live_now = pk in _LIVE_PAIRS
        last_hit = float(m.get("last_hit") or 0)
        fresh = bool(m.get("pinned")) or live_now or (now - last_hit < AUTO_RANGE_TTL) or not panels_up
        if not fresh:
            if now - float(m.get("last_seen") or 0) > 7 * 86400:
                expired.append(pk)
            continue
        if key not in merged:
            merged[key] = {"sid": _AUTO["names"].get(key, m.get("service", key)), "ranges": [],
                           "panels": [PANELS[0]["id"]], "src": {}}
            order.append(key)
        s = merged[key]
        if rid not in s["ranges"]:
            s["ranges"].append(rid)
            s["src"][rid] = "pinned" if m.get("pinned") else ("live" if live_now else m.get("source", "traffic"))
            if m.get("panel") and rid not in _RANGE_PANEL:
                _RANGE_PANEL[rid] = m["panel"]
        mm = meta.setdefault(rid, {"flag": "", "country": "", "hot": False, "hits": 0})
        if m.get("flag") and not mm.get("flag"):
            mm["flag"] = m["flag"]
        if m.get("country") and not mm.get("country"):
            mm["country"] = m["country"]
        h = _range_hits(m)
        mm["hits"] = max(int(mm.get("hits") or 0), h)
        if h >= AUTO_HOT_HITS or m.get("hot"):
            mm["hot"] = True
    for pk in expired:
        _AUTO["pairs"].pop(pk, None)
    for key in order:                                        # 3) fill missing country meta
        for rid in merged[key]["ranges"]:
            mm = meta.setdefault(rid, {"flag": "", "country": "", "hot": False, "hits": 0})
            if not mm.get("country"):
                mm["flag"], mm["country"] = get_country_info(_rid_prefix(rid))
    def _rk(rid, s):
        mm = meta.get(rid, {})
        return (0 if mm.get("hot") else 1, -int(mm.get("hits") or 0), -range_success_rate(rid), 0 if s["src"].get(rid) in ("live", "pinned") else 1)
    out = []
    for key in order:
        s = merged[key]
        s["ranges"].sort(key=lambda r, s=s: _rk(r, s))
        s["traffic"] = sum(int(meta.get(r, {}).get("hits") or 0) for r in s["ranges"])
        s["auto"] = sum(1 for r in s["ranges"] if s["src"].get(r) not in ("live",))
        out.append(s)
    live_keys = {_svc_key(x["sid"]) for x in _LIVE_RAW}
    out.sort(key=lambda s: (-s["traffic"], 0 if _svc_key(s["sid"]) in live_keys else 1))
    _RANGE_META.clear(); _RANGE_META.update(meta)
    for s in out:
        for r in s["ranges"]:
            _RANGE_PANEL.setdefault(r, PANELS[0]["id"])
    if out:
        _liveaccess_cache["services"] = out
    return out

def range_country(range_text):
    """(flag, country) for a range id — discovered meta first, dial-prefix map second."""
    mm = _RANGE_META.get(_norm_rid(range_text)) or {}
    if mm.get("country"):
        return (mm.get("flag") or "🌍"), mm["country"]
    return get_country_info(_rid_prefix(range_text))

async def _scan_console(p):
    """Read the global OTP console and auto-register every range / country / service seen there."""
    try:
        if _is_2oo9(p):
            d2 = await _2oo9_get(p, "console")
            rows = []
            for h in d2.get("hits") or []:
                if not isinstance(h, dict) or not _good_sid(h.get("sid")):
                    continue
                rg = h.get("range", "")
                f, c = get_country_info(_rid_prefix(rg))
                rows.append([rg, f"{f} {c}" if c != "Unknown" else "", h.get("message", ""), h.get("sid", ""), h.get("time")])
            d = {"table": rows}
        else:
            r = await p["client"].get(f"{p['base_url']}/api/console_data")
            d = r.json()
        rows = d.get("table") if isinstance(d, dict) else None
        if rows is None:
            raise RuntimeError((d or {}).get("error", "bad response") if isinstance(d, dict) else "bad response")
    except Exception as e:
        _AUTO_STATE["console_ok"] = False
        if str(e):
            print(f"[auto] console error: {e}")
        return []
    now = time.time()
    new_pairs = []
    for row in rows:
        try:
            rid, cty, msg, svc, ts = (list(row) + [None] * 5)[:5]
        except Exception:
            continue
        rid_n = _norm_rid(rid)
        if "X" not in rid_n:                     # a full number is not a range -> live feed only
            live_feed_push(p, rid, cty, msg, svc, ts, is_number=True)
            continue
        svc = str(svc or "").strip()
        if svc.lower() in ("", "unknown", "none"):
            guess = detect_service(msg)
            if guess == "SMS SERVICE":
                continue
            svc = guess.title()
        flag, country = _split_country(cty)
        try:
            t = float(ts)
            t = t / 1000.0 if t > 1e12 else t
        except Exception:
            t = now
        if now - t > 24 * 3600:
            continue
        dk = f"{int(t * 1000)}|{rid_n}|{hash(str(msg))}"
        hit = dk not in _TRAFFIC_SEEN
        if hit:
            _TRAFFIC_SEEN[dk] = (t, f"{flag} {country}".strip(), svc, rid_n)
            live_feed_push(p, rid_n, cty, msg, svc, t)
        if auto_upsert(svc, rid_n, flag, country, source="traffic", ts=t, hit=hit, panel=p["id"]):
            new_pairs.append((svc, rid_n, flag, country))
    for k in [k for k, v in _TRAFFIC_SEEN.items() if now - v[0] > 24 * 3600]:
        _TRAFFIC_SEEN.pop(k, None)
    _AUTO_STATE.update({"console_ok": True, "last_scan": datetime.now(), "rows": len(rows), "scans": _AUTO_STATE["scans"] + 1})
    return new_pairs

# ==================== LIVE OTP FEED -> OTP GROUP (v15) ====================
# Every REAL OTP that hits YESMS + STEXSMS (global console traffic) is posted to OTP_GROUP_ID
# automatically. Numbers are masked, ranges are shown as XXX. Telegram allows ~20 msgs/min
# in one group, so when traffic is high the feed batches several OTPs into one card -
# nothing is dropped, nothing is invented. Admin: /livefeed on|off
from collections import deque
LIVE_FEED_INTERVAL = 3.6            # seconds between group posts (~16/min, under Telegram's 20/min)
LIVE_FEED_BATCH_MAX = 12            # OTPs per digest card when traffic is high
LIVE_FEED_MAX_AGE = 15 * 60         # only OTPs younger than 15 min are posted
_LIVE_FEED_Q = deque(maxlen=5000)
_LIVE_FEED_SEEN = {}                # dedupe key -> ts
_LIVE_FEED_STATE = {"boot": time.time(), "sent_otps": 0, "sent_msgs": 0, "last_err": ""}
_OWN_GROUP_CODES = {}               # codes the monitor already posted for our own users (no duplicates)


def live_feed_enabled():
    try:
        return bool(load_bot_settings().get("live_feed", True))
    except Exception:
        return True


def live_feed_push(p, rid, cty, msg, svc, ts, is_number=False):
    """Queue one real console hit for the group feed (dedupe + freshness guard)."""
    try:
        msg = str(msg or "").strip()
        if not msg:
            return
        try:
            t = float(ts)
            t = t / 1000.0 if t > 1e12 else t
        except Exception:
            t = time.time()
        # never replay history on boot; never post stale codes
        if t < _LIVE_FEED_STATE["boot"] - 60 or time.time() - t > LIVE_FEED_MAX_AGE:
            return
        code = extract_otp(msg) or ""
        norm_msg = " ".join(msg.split())
        key = f"{code}|{hash(norm_msg)}"           # same SMS seen as range row + number row = one OTP
        if key in _LIVE_FEED_SEEN:
            return
        _LIVE_FEED_SEEN[key] = t
        svc = str(svc or "").strip()
        if svc.lower() in ("", "unknown", "none") or re.fullmatch(r"[+\d\s]+", svc):
            g = detect_service(msg)
            svc = g.title() if g != "SMS SERVICE" else "SMS"
        flag, country = _split_country(cty)
        raw = re.sub(r"\D", "", str(rid)) if is_number else _rid_prefix(rid)
        if not country or country == "Unknown":
            flag, country = get_country_info(raw)
        if is_number:
            num = re.sub(r"\D", "", str(rid))
            if normalize_number(num) in active_numbers:      # our own user -> the monitor posts it already
                return
            shown = mask_number("+" + num)
        else:
            shown = _norm_rid(rid)
        _LIVE_FEED_Q.append({"svc": svc, "flag": flag or "🌍", "country": country or "Unknown",
                             "shown": shown, "is_number": is_number, "code": code, "msg": msg, "ts": t})
    except Exception as e:
        print(f"[feed] push error: {e}")


def _feed_item_card(it, to_range_group=False):
    """v16.14 one-by-one feed card — OTP group: SERVICE + OTP · range group: RANGE + SERVICE + OTP."""
    star = "⭐️" * 15
    lines = [star, f"⭐️ <b>{BRAND_NAME}</b> ⭐️"]
    if to_range_group:
        lines.append(f"⭐️✨ <b>{BRAND_NAME} RANGE HIT</b> ✨⭐️")
        lines.append(f"📊 <b>Range</b> : <code>{html.escape(str(it['shown']))}</code>")
    else:
        lines.append(f"⭐️✨ <b>{BRAND_NAME} OTP Received</b> ✨⭐️")
    lines.append(f"⚙️ <b>Service</b> : {service_icon(it['svc'])} {html.escape(str(it['svc']))}")
    if it.get("code"):
        lines.append(f"🔑 <b>OTP Code</b> : <code>{html.escape(it['code'])}</code>")
    lines.append("")
    lines.append(f"<blockquote>{html.escape(str(it['msg'])[:600])}</blockquote>")
    lines.append("⭐️──────────────────⭐️")
    return "\n".join(lines)


def _feed_kb(code=None):
    row = [make_inline_keyboard_button(pbtn("🤖", "GET NUMBER"), url="https://t.me/numbergenfast_bot", style="success")]
    rows = [row]
    if code:
        cp = copy_button("🔑 COPY OTP", code)
        if cp:
            rows.insert(0, [cp])
    return InlineKeyboardMarkup(rows)


LIVE_FEED_PHOTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "otp_feed_logo.png")
_LIVE_FEED_PHOTO_ID = None            # cached Telegram file_id of the feed logo (uploaded once)


async def _feed_post(application, text, kb):
    """Post one feed card to the OTP group as a premium photo + caption card.

    The gold logo is uploaded once and then sent by file_id (fast, no re-uploads).
    If anything photo-related fails, it falls back to a plain text card so no OTP is lost.
    """
    global _LIVE_FEED_PHOTO_ID
    bot = application.bot
    caption = text if len(text) <= 1020 else text[:1017] + "…"
    if _LIVE_FEED_PHOTO_ID:
        try:
            return await bot.send_photo(OTP_GROUP_ID, _LIVE_FEED_PHOTO_ID, caption=caption,
                                        parse_mode="HTML", reply_markup=kb)
        except Exception:
            _LIVE_FEED_PHOTO_ID = None                    # cached id rejected -> re-upload once
    try:
        with open(LIVE_FEED_PHOTO, "rb") as fh:
            msg = await bot.send_photo(OTP_GROUP_ID, fh, caption=caption,
                                       parse_mode="HTML", reply_markup=kb)
        try:
            _LIVE_FEED_PHOTO_ID = msg.photo[-1].file_id
        except Exception:
            pass
        return msg
    except Exception:
        return await bot.send_message(OTP_GROUP_ID, caption, parse_mode="HTML",
                                      reply_markup=kb, disable_web_page_preview=True)


async def live_feed_loop(application):
    """Drain the feed queue into OTP_GROUP_ID at a Telegram-safe pace."""
    await asyncio.sleep(10)
    while True:
        try:
            _LOOP_TICK["feed"] = time.time()
            now = time.time()
            for k in [k for k, v in _LIVE_FEED_SEEN.items() if now - v > 3 * 3600]:
                _LIVE_FEED_SEEN.pop(k, None)
            for k in [k for k, v in _OWN_GROUP_CODES.items() if now - v > 1800]:
                _OWN_GROUP_CODES.pop(k, None)
            if not live_feed_enabled():
                _LIVE_FEED_Q.clear()
            else:
                # v16.14: ONE OTP per message — never batched. Ranges → range group, OTPs → OTP group.
                it = None
                while _LIVE_FEED_Q:
                    cand = _LIVE_FEED_Q.popleft()
                    if cand["code"] and cand["code"] in _OWN_GROUP_CODES:
                        continue                      # already posted with the owner's details
                    it = cand
                    break
                if it:
                    to_range = not it["is_number"]
                    target = range_group_id() if to_range else OTP_GROUP_ID
                    if to_range and not target:
                        # v16.15: range group not set → drop the range hit (never leak into the OTP group)
                        continue
                    text = _feed_item_card(it, to_range_group=to_range)
                    kb = _feed_kb(it["code"]) if not to_range else otp_group_buttons()
                    try:
                        await application.bot.send_message(target, text, parse_mode="HTML",
                                                            reply_markup=kb, disable_web_page_preview=True)
                        _LIVE_FEED_STATE["sent_otps"] += 1
                        _LIVE_FEED_STATE["sent_msgs"] += 1
                    except RetryAfter as e:
                        _LIVE_FEED_Q.appendleft(it)
                        wait = float(getattr(e, "retry_after", 5) or 5)
                        print(f"[feed] flood control - waiting {wait}s")
                        await asyncio.sleep(wait)
                    except Exception as e:
                        _LIVE_FEED_STATE["last_err"] = str(e)[:120]
                        print(f"[feed] send fail: {e}")
        except Exception as e:
            print(f"[feed] loop error: {e}")
        await asyncio.sleep(LIVE_FEED_INTERVAL)


async def livefeed_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    arg = (context.args[0].lower() if context.args else "")
    st = load_bot_settings()
    if arg in ("on", "off"):
        st["live_feed"] = arg == "on"
        save_bot_settings(st)
    on = live_feed_enabled()
    total, _, _ = network_traffic(1)
    f = _LIVE_FEED_STATE
    err = f"\n⚠️ Last error: <code>{html.escape(f['last_err'])}</code>" if f["last_err"] else ""
    await update.message.reply_text(
        f"{head('LIVE OTP FEED', '🔴')}Status: <b>{'🟢 ON' if on else '🔴 OFF'}</b>\n"
        f"📤 Posted since boot: <code>{f['sent_otps']}</code> OTPs in <code>{f['sent_msgs']}</code> messages\n"
        f"⏳ In queue: <code>{len(_LIVE_FEED_Q)}</code>\n"
        f"🔑 Network OTPs (1h): <code>{total}</code>{err}\n{DIV}\n<i>/livefeed on · /livefeed off</i>",
        parse_mode="HTML")


async def feedphoto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the OTP group's profile photo to the gold LIVE OTP logo (needs admin rights in the group)."""
    if not is_admin(update.effective_user.id):
        return
    uid = update.effective_user.id
    try:
        with open(LIVE_FEED_PHOTO, "rb") as fh:
            await context.bot.set_chat_photo(OTP_GROUP_ID, fh)
        await context.bot.send_message(uid, f"✅ OTP group photo updated with the {head('LIVE OTP', '🔴')} gold logo.")
    except Exception as e:
        await context.bot.send_message(uid, f"❌ Could not set the group photo: {html.escape(str(e)[:150])}\n"
                                           f"<i>The bot must be an admin of the group with 'Change group info' rights.</i>",
                                       parse_mode="HTML")


def network_traffic(hours=1):
    """(total, {country_label: n}, {service: n}) seen on the global console in the last N hours."""
    since = time.time() - hours * 3600
    total, per_c, per_s = 0, {}, {}
    for t, cty, svc, rid in _TRAFFIC_SEEN.values():
        if t < since:
            continue
        total += 1
        per_c[cty or "🌍 Unknown"] = per_c.get(cty or "🌍 Unknown", 0) + 1
        per_s[svc] = per_s.get(svc, 0) + 1
    return total, per_c, per_s

async def _announce_new_ranges(application, new_pairs):
    try:
        # v16.15: RANGE announcements go ONLY to the range group — never into the OTP group
        target = range_group_id()
        if not target:
            print("[auto] range group not set — announcements skipped (send /setrangegroup inside the range group)")
            return
        lines = [head("🔥 AUTO RANGE ADDED", "🆕").rstrip("\n")]
        for svc, rid, flag, country in new_pairs[:12]:
            lines.append(f"{service_icon(svc)} <b>{html.escape(str(svc).upper())}</b> ▸ {flag or '🌍'} {html.escape(country or 'Unknown')} · <code>{rid}</code>")
        if len(new_pairs) > 12:
            lines.append(f"<i>…and {len(new_pairs) - 12} more</i>")
        lines.append(f"{DIV}\n<i>⚡ Live now in 📱 Get Number — auto-discovered from network traffic.</i>")
        await application.bot.send_message(target, "\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        print(f"[auto] announce fail: {e}")

async def auto_discovery_loop(application=None):
    _load_auto_catalog()
    while True:
        try:
            _LOOP_TICK["auto"] = time.time()
            new_pairs = []
            for p in active_panels():
                new_pairs += await _scan_console(p)
            _rebuild_services_cache()
            if _AUTO_DIRTY:
                _save_auto_catalog()
            if new_pairs:
                _AUTO_STATE["new_total"] += len(new_pairs)
                print(f"[auto] +{len(new_pairs)} new range(s): " + ", ".join(f"{s}:{r}" for s, r, *_ in new_pairs[:8]))
                if AUTO_ANNOUNCE and application is not None and _AUTO_STATE["booted"]:
                    await _announce_new_ranges(application, new_pairs)
            _AUTO_STATE["booted"] = True
        except Exception as e:
            print(f"[auto] loop error: {e}")
        await asyncio.sleep(AUTO_DISCOVERY_INTERVAL)

def auto_status_card():
    svcs = get_cached_services()
    ranges = {r for s in svcs for r in s["ranges"]}
    countries = {(_RANGE_META.get(r, {}).get("country") or "?") for r in ranges}
    n_live = sum(1 for s in svcs for r in s["ranges"] if s.get("src", {}).get(r) == "live")
    n_pin = sum(1 for s in svcs for r in s["ranges"] if s.get("src", {}).get(r) == "pinned")
    n_auto = len(ranges) and (sum(len(s["ranges"]) for s in svcs) - n_live - n_pin)
    total, per_c, per_s = network_traffic(1)
    st = _AUTO_STATE
    ok = "🟢 ONLINE" if st.get("console_ok") else ("🔴 OFFLINE" if st.get("console_ok") is False else "⏳ WAITING")
    last = st["last_scan"].strftime("%H:%M:%S") if st.get("last_scan") else "—"
    lines = [
        head("AUTO DISCOVERY", "🤖").rstrip("\n"),
        f"📡 <b>Console feed</b> ▸ {ok}  <i>(scan #{st.get('scans', 0)}, {last})</i>",
        f"🛰️ <b>Live access</b> ▸ {len(_LIVE_RAW)} service(s)",
        DIV_S,
        f"🎛️ <b>Services</b>  ▸ <code>{len(svcs)}</code>",
        f"🌍 <b>Countries</b> ▸ <code>{len(countries)}</code>",
        f"📦 <b>Ranges</b>    ▸ <code>{len(ranges)}</code> unique · {n_live + n_auto + n_pin} service-pairs  (live {n_live} · auto {n_auto} · pinned {n_pin})",
        f"🆕 <b>Learned since boot</b> ▸ <code>{st.get('new_total', 0)}</code>",
        f"🔑 <b>Network OTP / 1h</b> ▸ <code>{total}</code>",
        DIV_S,
        "<b>🔥 HOT RANGES (last hour)</b>",
    ]
    hot = sorted(((int(_RANGE_META.get(r, {}).get("hits") or 0), r) for r in ranges), reverse=True)[:8]
    for hits, r in hot:
        if hits <= 0:
            break
        f, c = range_country(r)
        lines.append(f"{f} {html.escape(c)} · <code>{r}</code> — <b>{hits}</b>/h")
    lines.append(f"{DIV}\n<i>/addrange SERVICE RANGE — pin ▸ /delrange SERVICE RANGE — unpin</i>")
    return "\n".join(lines)

async def setpick_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /setpick N — numbers handed out per GET NUMBER tap (unlimited)."""
    if not is_admin(update.effective_user.id):
        return
    args = context.args or []
    if args and re.sub(r"\D", "", args[0]):
        set_numbers_per_pick(int(re.sub(r"\D", "", args[0])))
    t, kb = _numpick_view()
    await update.message.reply_text(t, parse_mode="HTML", reply_markup=kb)

async def autodisc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(auto_status_card(), parse_mode="HTML")

async def addrange_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text("Usage: <code>/addrange Facebook 26661XXX</code>", parse_mode="HTML")
        return
    rid = _norm_rid(args[-1]); svc = " ".join(args[:-1])
    if not re.match(r"^\d{2,}X", rid):
        await update.message.reply_text("⚠️ Range must look like <code>26661XXX</code> or <code>249X1</code>", parse_mode="HTML")
        return
    auto_upsert(svc, rid, source="manual", touch=True)
    _rebuild_services_cache(); _save_auto_catalog()
    f, c = range_country(rid)
    await update.message.reply_text(f"✅ Pinned {service_icon(svc)} <b>{html.escape(svc.upper())}</b> ▸ {f} {html.escape(c)} · <code>{rid}</code>", parse_mode="HTML")

async def delrange_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text("Usage: <code>/delrange Facebook 26661XXX</code>", parse_mode="HTML")
        return
    rid = _norm_rid(args[-1]); key = _svc_key(" ".join(args[:-1]))
    gone = _AUTO["pairs"].pop(f"{key}|{rid}", None)
    _rebuild_services_cache(); _save_auto_catalog()
    await update.message.reply_text("🗑️ Removed." if gone else "⚠️ Not found in the auto catalogue.", parse_mode="HTML")


# ==================== WORLD-CLASS PACK (v12) ====================
RANGE_STATS_FILE = "range_stats.json"
QUICK_PICK_FILE = "quick_pick.json"
DAILY_BONUS_FILE = "daily_bonus.json"
OTP_WAIT_SEC = 15 * 60               # countdown length on a fresh number card
COUNTDOWN_STEP = 30                  # seconds between card refreshes
BOT_STARTED_AT = time.time()
_LOOP_TICK = {}                      # loop name → last tick ts (for /health)
_OTP_DONE = {}                       # number → otp code (countdown stops instantly)
_RELEASED = set()                    # numbers the user released (never paid again)
_ALERT_LAST = {}                     # panel id → last alert ts
_RANGE_STATS = None

def _rs_load():
    global _RANGE_STATS
    if _RANGE_STATS is None:
        try:
            _RANGE_STATS = json.load(open(RANGE_STATS_FILE, encoding="utf-8")) if os.path.exists(RANGE_STATS_FILE) else {}
        except Exception:
            _RANGE_STATS = {}
    return _RANGE_STATS

def _rs_save():
    try:
        json.dump(_rs_load(), open(RANGE_STATS_FILE, "w", encoding="utf-8"))
    except Exception:
        pass

def range_stat_bump(rid, key):
    """key = 'alloc' | 'otp' — feeds the ✅ success-rate badge and AUTO BEST ranking."""
    rid = _norm_rid(rid)
    if not rid:
        return
    d = _rs_load().setdefault(rid, {"alloc": 0, "otp": 0})
    d[key] = int(d.get(key, 0)) + 1
    _rs_save()

def range_success_rate(rid, min_alloc=5):
    d = _rs_load().get(_norm_rid(rid)) or {}
    a, o = int(d.get("alloc", 0)), int(d.get("otp", 0))
    return int(round(100.0 * min(o, a) / a)) if a >= min_alloc else -1

def _qp_load():
    try:
        return json.load(open(QUICK_PICK_FILE, encoding="utf-8")) if os.path.exists(QUICK_PICK_FILE) else {}
    except Exception:
        return {}

def quick_pick_set(uid, sid, range_text):
    d = _qp_load(); d[str(uid)] = {"sid": sid, "range": range_text, "ts": time.time()}
    try:
        json.dump(d, open(QUICK_PICK_FILE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass

def quick_pick_get(uid):
    return _qp_load().get(str(uid))

def release_number(num, uid=None):
    """Stop monitoring/paying a number (user pressed 🗑️ RELEASE or 🔁 CHANGE)."""
    num = normalize_number(num)
    if not num:
        return False
    owner = active_numbers.get(num)
    if uid is not None and owner and str(owner.get("uid")) != str(uid):
        return False
    active_numbers.pop(num, None)
    _RELEASED.add(num)
    try:
        db = load_range_db()
        if num in db and (uid is None or str(db[num].get("user_id")) == str(uid)):
            db[num]["released"] = True
            save_range_db(db)
    except Exception:
        pass
    return True

def _bar(frac, n=10):
    k = max(0, min(n, int(round(frac * n))))
    return "▰" * k + "▱" * (n - k)

async def otp_countdown(bot, chat_id, message_id, num, base_text, lang="en"):
    """Live ⏳ footer on a fresh number card; flips to ✅ when the OTP lands or ⌛ on expiry."""
    num = normalize_number(num)
    start = time.time()
    while True:
        await asyncio.sleep(COUNTDOWN_STEP)
        if num in _RELEASED:
            return
        code = _OTP_DONE.get(num)
        left = OTP_WAIT_SEC - (time.time() - start)
        if code:
            foot = f"\n\n✅ <b>OTP RECEIVED</b> ▸ <code>{html.escape(str(code))}</code>  🎉"
            kb = number_actions_kb(lang, num, code)
        elif left <= 0 or num not in active_numbers:
            if num not in active_numbers and left > 0:
                return
            foot = "\n\n⌛ <b>Expired</b> — no OTP arrived in 15 min. Tap 🔁 CHANGE NUMBER."
            kb = number_actions_kb(lang, num, None)
        else:
            m, s = divmod(int(left), 60)
            foot = f"\n\n⏳ <b>Waiting for OTP</b> · <code>{m:02d}:{s:02d}</code> left\n<code>{_bar(left / OTP_WAIT_SEC)}</code>"
            kb = number_actions_kb(lang, num, None)
        try:
            await bot.edit_message_text(base_text + foot, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            if "not modified" not in str(e).lower():
                return
        if code or left <= 0:
            return

def my_numbers_card(uid, limit=10):
    db = load_range_db()
    paid = load_data(PAID_SMS_FILE) if os.path.exists(PAID_SMS_FILE) else {}
    codes = {}
    for k, v in (paid or {}).items():
        if str(v.get("uid")) == str(uid) and "_" in k:
            codes[k.split("_", 1)[0]] = v.get("otp")
    mine = [(n, d) for n, d in db.items() if str(d.get("user_id")) == str(uid)]
    mine.sort(key=lambda x: float(x[1].get("ts") or 0), reverse=True)
    lines = [head("MY NUMBERS", "📜").rstrip("\n")]
    if not mine:
        lines.append("<i>No numbers yet — tap 📞 GET NUMBER.</i>")
    now = time.time()
    for n, d in mine[:limit]:
        age = now - float(d.get("ts") or 0)
        if n in codes:
            st = f"✅ <code>{html.escape(str(codes[n]))}</code>"
        elif d.get("released"):
            st = "🗑️ released"
        elif age < OTP_WAIT_SEC:
            st = "⏳ waiting"
        else:
            st = "⌛ expired"
        when = datetime.fromtimestamp(float(d.get("ts") or now)).strftime("%d %b %H:%M")
        lines.append(f"🔥 📟 <code>+{n}</code> · {html.escape(str(d.get('country') or ''))} · <code>{html.escape(str(d.get('range') or ''))}</code>\n      {st} · <i>{when}</i>")
    total_otp = sum(1 for n, _ in mine if n in codes)
    lines.append(f"{DIV_S}\n📦 Total: <b>{len(mine)}</b> · ✅ OTP: <b>{total_otp}</b> · 🎯 Rate: <b>{(100 * total_otp // len(mine)) if mine else 0}%</b>\n{DIV}")
    return "\n".join(lines)

def my_numbers_kb():
    return InlineKeyboardMarkup([[refresh_button("my_numbers"), close_button()]])

def _daily_cfg():
    try:
        return float(load_bot_settings().get("daily_bonus", 0.10))
    except Exception:
        return 0.10

async def claim_daily_bonus(uid):
    """Returns (ok, amount, streak, next_in_sec)."""
    try:
        d = json.load(open(DAILY_BONUS_FILE, encoding="utf-8")) if os.path.exists(DAILY_BONUS_FILE) else {}
    except Exception:
        d = {}
    rec = d.get(str(uid), {"last": 0, "streak": 0})
    now = time.time()
    since = now - float(rec.get("last") or 0)
    if since < 24 * 3600:
        return False, 0.0, int(rec.get("streak", 0)), int(24 * 3600 - since)
    streak = int(rec.get("streak", 0)) + 1 if since < 48 * 3600 else 1
    base = _daily_cfg()
    amount = round(base * min(streak, 7), 2)
    await update_db_balance(uid, amount)
    d[str(uid)] = {"last": now, "streak": streak}
    try:
        json.dump(d, open(DAILY_BONUS_FILE, "w", encoding="utf-8"))
    except Exception:
        pass
    log_global_activity(uid, "DAILY_BONUS", {"amount": amount, "streak": streak})
    return True, amount, streak, 24 * 3600

def daily_card(ok, amount, streak, next_in):
    h, m = divmod(next_in // 60, 60)
    if ok:
        return (f"{head('DAILY BONUS', '🎁')}🪙 <b>+{amount:.2f} ৳</b> added to your wallet!\n"
                f"🔥 Streak ▸ <b>day {streak}</b> {'🏆' if streak >= 7 else ''}\n"
                f"<i>Come back in 24h to keep the streak — up to x7 bonus.</i>\n{DIV}")
    return (f"{head('DAILY BONUS', '🎁')}⏳ Already claimed. Next bonus in <b>{h}h {m:02d}m</b>\n"
            f"🔥 Streak ▸ <b>day {streak}</b>\n{DIV}")

def get_max_active():
    try:
        return max(0, int(load_bot_settings().get("max_active", 0)))
    except Exception:
        return 0

async def _probe_panel(base_url, api_key):
    """Try every known auth header against /api/live_access_data. Returns (header, bearer, services) or (None, None, err)."""
    base_url = base_url.rstrip("/")
    last_err = "no response"
    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as c:
        try:                                                      # 2oo9 CLOUD style (mauthapi)
            r = await c.get(f"{base_url}/liveaccess", headers={"mauthapi": api_key})
            d = r.json()
            if isinstance(d, dict) and (d.get("meta") or {}).get("code") == 200:
                return "mauthapi", False, len((d.get("data") or {}).get("services") or [])
        except Exception:
            pass
        for hdr in AUTH_HEADER_CANDIDATES:
            for bearer in ((False, True) if hdr.lower() == "authorization" else (False,)):
                try:
                    val = f"Bearer {api_key}" if bearer else api_key
                    r = await c.get(f"{base_url}/api/live_access_data", headers={hdr: val})
                    d = r.json()
                    if isinstance(d, dict) and not d.get("error") and ("ranges" in d or "services" in d):
                        return hdr, bearer, len(d.get("ranges") or {})
                    last_err = str((d or {}).get("error") if isinstance(d, dict) else d)[:80] or f"HTTP {r.status_code}"
                except Exception as e:
                    last_err = str(e)[:80] or type(e).__name__
        try:                                                      # query-param style
            r = await c.get(f"{base_url}/api/live_access_data", params={"api_key": api_key})
            d = r.json()
            if isinstance(d, dict) and not d.get("error") and ("ranges" in d or "services" in d):
                return "?api_key", False, len(d.get("ranges") or {})
        except Exception:
            pass
    return None, None, last_err

async def addpanel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: /addpanel NAME https://panel.tld APIKEY — auto-detects auth & plugs the panel in."""
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    args = context.args or []
    if len(args) < 3:
        await update.message.reply_text(
            f"{head('ADD PANEL', '🛰️')}Usage:\n<code>/addpanel NAME https://panel.tld APIKEY</code>\n"
            f"<i>Example:</i> <code>/addpanel FASTX https://fastx.site 1a2b3c4d</code>\n{DIV}",
            parse_mode="HTML")
        return
    name, url, key = args[0].strip(), args[1].strip().rstrip("/"), " ".join(args[2:]).strip()
    if not url.startswith("http"):
        url = "https://" + url
    pid = re.sub(r"[^a-z0-9]", "", name.lower()) or f"p{len(PANELS) + 1}"
    wait = await update.message.reply_text(f"🔎 Probing <b>{html.escape(name)}</b> … testing {len(AUTH_HEADER_CANDIDATES) + 1} auth styles", parse_mode="HTML")
    hdr, bearer, info = await _probe_panel(url, key)
    if not hdr:
        await wait.edit_text(
            f"{head('PANEL REJECTED', '❌')}<b>{html.escape(name)}</b> ▸ <code>{html.escape(url)}</code>\n"
            f"⚠️ Could not authenticate: <code>{html.escape(str(info))}</code>\n\n"
            f"<i>Check the URL/key. This bot auto-connects any yehub-style panel "
            f"(/api/live_access_data, /api/allocate_number, /api/success_logs). For a different API, send me its docs.</i>\n{DIV}",
            parse_mode="HTML")
        return
    old = next((p for p in PANELS if p["id"] == pid), None)
    if old:
        try:
            await old["client"].aclose()
        except Exception:
            pass
        PANELS.remove(old)
    p = {"id": pid, "name": name.upper(), "base_url": url, "api_key": key, "dynamic": True,
         "auth_header": "X-API-Key" if hdr == "?api_key" else hdr, "bearer": bool(bearer),
         "kind": "2oo9" if hdr == "mauthapi" else "yesms"}
    p["client"] = _mk_client(p)
    if hdr == "?api_key":
        p["client"].params = {"api_key": key}
    p["enabled"] = True
    p["status"] = {"online": True, "services": 0, "last_ok": datetime.now(), "error": ""}
    PANELS.append(p)
    _save_dynamic_panels()
    await _do_liveaccess_fetch()
    try:
        await _scan_console(p); _rebuild_services_cache()
    except Exception:
        pass
    svcs = get_cached_services()
    mine = [s for s in svcs if pid in (s.get("panels") or [])]
    await wait.edit_text(
        f"{head('PANEL CONNECTED', '✅')}🛰️ <b>{html.escape(p['name'])}</b> ▸ <code>{html.escape(url)}</code>\n"
        f"🔐 Auth ▸ <code>{html.escape(hdr)}</code>{' (Bearer)' if bearer else ''}\n"
        f"🎛️ Services ▸ <b>{len(mine)}</b> · 📦 Ranges ▸ <b>{sum(len(s['ranges']) for s in mine)}</b>\n"
        f"🧩 Total panels online ▸ <b>{len(active_panels())}</b>\n{DIV}\n"
        f"<i>GET NUMBER now merges every panel; 🔥 marks ranges with live traffic.</i>",
        parse_mode="HTML")

async def delpanel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = context.args or []
    pid = re.sub(r"[^a-z0-9]", "", (args[0] if args else "").lower())
    p = next((x for x in PANELS if x["id"] == pid), None)
    if not p:
        await update.message.reply_text("Usage: <code>/delpanel NAME</code>  ·  panels: " + ", ".join(f"<code>{x['id']}</code>" for x in PANELS), parse_mode="HTML")
        return
    if not p.get("dynamic"):
        await update.message.reply_text("⚠️ Built-in panels (YESMS, STEXSMS) can't be removed — edit CONFIG instead.", parse_mode="HTML")
        return
    PANELS.remove(p)
    _save_dynamic_panels()
    await _do_liveaccess_fetch()
    await update.message.reply_text(f"🗑️ Panel <b>{html.escape(p['name'])}</b> removed. {len(active_panels())} panel(s) online.", parse_mode="HTML")

async def alert_admins(bot, text, key="generic", throttle=600):
    now = time.time()
    if now - _ALERT_LAST.get(key, 0) < throttle:
        return
    _ALERT_LAST[key] = now
    for aid in get_all_admins():
        try:
            await bot.send_message(aid, text, parse_mode="HTML")
        except Exception:
            pass

async def panel_watchdog_loop(application):
    """Admin DM when a panel goes offline / comes back; keeps /health timestamps."""
    prev = {}
    while True:
        try:
            _LOOP_TICK["watchdog"] = time.time()
            for p in active_panels():
                st = p.get("status") or {}
                on = st.get("online")
                if on is None:
                    continue
                if prev.get(p["id"]) is True and on is False:
                    await alert_admins(application.bot,
                        f"{head('PANEL OFFLINE', '🚨')}🛰️ <b>{html.escape(p['name'])}</b> stopped answering\n"
                        f"⚠️ <code>{html.escape(st.get('error') or 'timeout')}</code>\n<i>GET NUMBER keeps working from the other panels.</i>",
                        key=f"off_{p['id']}")
                elif prev.get(p["id"]) is False and on is True:
                    await alert_admins(application.bot, f"✅ <b>{html.escape(p['name'])}</b> is back online.", key=f"on_{p['id']}", throttle=60)
                prev[p["id"]] = on
        except Exception as e:
            print(f"[watchdog] {e}")
        await asyncio.sleep(20)

def _otp_today_count():
    try:
        g = get_global_system_stats()
        if isinstance(g, dict):
            return g.get("today_otps", 0)
        return g[1] if isinstance(g, (tuple, list)) and len(g) > 1 else "—"
    except Exception:
        return "—"

def health_card():
    up = int(time.time() - BOT_STARTED_AT)
    d, r = divmod(up, 86400); h, r = divmod(r, 3600); m, _ = divmod(r, 60)
    now = time.time()
    def tick(name, limit):
        t = _LOOP_TICK.get(name)
        return "🟢" if t and now - t < limit else "🔴"
    lines = [head("HEALTH", "🩺").rstrip("\n"),
             f"⏱️ Uptime ▸ <b>{d}d {h}h {m}m</b>",
             f"🧩 Panels ▸ " + " · ".join(f"{'🟢' if (p.get('status') or {}).get('online') else '🔴'} {html.escape(p['name'])}" for p in active_panels()),
             f"🩺 Panel errors ▸ " + (" · ".join(f"{html.escape(p['name'])}: <code>{html.escape(str((p.get('status') or {}).get('error') or '-')[:60])}</code>" for p in active_panels())),
             f"🗂️ Range group ▸ " + (f"<code>{range_group_id()}</code>" if range_group_id() else "⚠️ not set — send /setrangegroup inside the range group"),
             f"💰 Panel rates ▸ " + " · ".join(f"{html.escape(p['name'])} <code>{panel_rate(p['id']):.2f}৳</code>" for p in active_panels()),
             f"{tick('monitor', 15)} OTP monitor · {tick('auto', 40)} Auto-discovery · {tick('live', 90)} Live access · {tick('watchdog', 60)} Watchdog",
             f"📟 Active numbers ▸ <b>{len(active_numbers)}</b> · 👥 Users ▸ <b>{len(get_all_users())}</b>",
             f"🔑 OTP today ▸ <b>{_otp_today_count()}</b>",
             f"🤖 Catalogue ▸ {len(get_cached_services())} services · {sum(len(s['ranges']) for s in get_cached_services())} ranges",
             DIV]
    return "\n".join(lines)

async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text(health_card(), parse_mode="HTML")

def make_backup_zip():
    import zipfile
    name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in sorted(os.listdir(".")):
            if fn.endswith(".json"):
                z.write(fn)
    buf.seek(0); buf.name = name
    return buf

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    try:
        await update.message.reply_document(make_backup_zip(), caption=f"💾 <b>Full data backup</b> · {datetime.now():%d %b %Y %H:%M}", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Backup failed: {html.escape(str(e))}", parse_mode="HTML")

async def auto_backup_loop(application):
    while True:
        await asyncio.sleep(24 * 3600)
        for aid in get_all_admins():
            try:
                await application.bot.send_document(aid, make_backup_zip(), caption="💾 <b>Daily auto-backup</b>", parse_mode="HTML")
            except Exception:
                pass

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(my_numbers_card(update.effective_user.id), parse_mode="HTML", reply_markup=my_numbers_kb())

async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ok, amt, streak, nxt = await claim_daily_bonus(uid)
    if ok:
        await send_fx(context.bot, uid, daily_card(ok, amt, streak, nxt), effect="party")
    else:
        await update.message.reply_text(daily_card(ok, amt, streak, nxt), parse_mode="HTML")

async def setdaily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    try:
        v = float((context.args or [""])[0])
        cfg = load_bot_settings(); cfg["daily_bonus"] = max(0.0, v); save_bot_settings(cfg)
        await update.message.reply_text(f"✅ Daily bonus base set to <b>{v:.2f} ৳</b> (x streak, max x7).", parse_mode="HTML")
    except Exception:
        await update.message.reply_text(f"Usage: <code>/setdaily 0.20</code> — current <b>{_daily_cfg():.2f} ৳</b>", parse_mode="HTML")

async def setmaxactive_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    try:
        v = int((context.args or [""])[0])
        cfg = load_bot_settings(); cfg["max_active"] = max(0, v); save_bot_settings(cfg)
        await update.message.reply_text(f"✅ Max active numbers per user ▸ <b>{v or 'unlimited'}</b>", parse_mode="HTML")
    except Exception:
        await update.message.reply_text(f"Usage: <code>/setmaxactive 20</code> (0 = unlimited) — current <b>{get_max_active() or 'unlimited'}</b>", parse_mode="HTML")

# ==================== MULTI-ADMIN SYSTEM ====================

def load_dynamic_admins():
    if not os.path.exists(ADMINS_FILE):
        with open(ADMINS_FILE, "w") as f:
            json.dump([], f)
        return []
    try:
        with open(ADMINS_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def save_dynamic_admins(admins):
    with open(ADMINS_FILE, "w") as f:
        json.dump(admins, f, indent=4)

def get_all_admins():
    seen, result = set(), []
    for a in BASE_ADMINS:
        seen.add(int(a))
        result.append(int(a))
    for a in load_dynamic_admins():
        try:
            ai = int(a)
        except Exception:
            continue
        if ai not in seen:
            seen.add(ai)
            result.append(ai)
    return result

def is_admin(user_id):
    try:
        return int(user_id) in get_all_admins()
    except Exception:
        return False

def is_super_admin(user_id):
    try:
        return int(user_id) in BASE_ADMINS
    except Exception:
        return False

def add_dynamic_admin(uid):
    uid = str(uid)
    admins = load_dynamic_admins()
    if uid not in admins:
        admins.append(uid)
        save_dynamic_admins(admins)
        return True
    return False

def remove_dynamic_admin(uid):
    uid = str(uid)
    admins = load_dynamic_admins()
    if uid in admins:
        admins.remove(uid)
        save_dynamic_admins(admins)
        return True
    return False

# ==================== WELCOME VIDEO SYSTEM ====================

def load_welcome_videos():
    if not os.path.exists(WELCOME_VIDEOS_FILE):
        with open(WELCOME_VIDEOS_FILE, "w") as f:
            json.dump([], f)
        return []
    try:
        with open(WELCOME_VIDEOS_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def save_welcome_videos(vids):
    with open(WELCOME_VIDEOS_FILE, "w") as f:
        json.dump(vids, f, indent=4)

async def send_welcome_videos(chat_id, context, lang):
    """Fire the admin-configured starter videos to freshly verified users."""
    vids = load_welcome_videos()
    if not vids:
        return False
    intro_txt = f"{head('HOW TO USE', '🎬')}<b>Watch these quick guide videos before you start 👇</b>"
    try:
        await context.bot.send_message(chat_id=chat_id, text=intro_txt, parse_mode="HTML")
    except Exception:
        pass
    for fid in vids[:MAX_WELCOME_VIDEOS]:
        try:
            await context.bot.send_video(chat_id=chat_id, video=fid, caption=f"🎬 {BRAND_SHORT}", parse_mode="HTML")
        except Exception as e:
            print(f"welcome video send fail: {e}")
    return True

# ==================== SERVICE VISIBILITY CONTROL (ADMIN SHOW/HIDE) ====================

def load_hidden_services():
    if not os.path.exists(HIDDEN_SERVICES_FILE):
        with open(HIDDEN_SERVICES_FILE, "w") as f:
            json.dump([], f)
        return []
    try:
        with open(HIDDEN_SERVICES_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def save_hidden_services(sids):
    with open(HIDDEN_SERVICES_FILE, "w") as f:
        json.dump(sids, f, indent=4)

def is_service_hidden(sid):
    return str(sid) in load_hidden_services()

def toggle_service_hidden(sid):
    sid = str(sid)
    hidden = load_hidden_services()
    if sid in hidden:
        hidden.remove(sid)
        save_hidden_services(hidden)
        return False  # now visible
    hidden.append(sid)
    save_hidden_services(hidden)
    return True  # now hidden

def filter_visible_services(services):
    hidden = set(load_hidden_services())
    if not hidden:
        return list(services)
    return [s for s in services if str(s.get("sid")) not in hidden]

# ==================== BOT SETTINGS (ADMIN TUNABLE) ====================

def load_bot_settings():
    defaults = {"numbers_per_pick": 1}
    if not os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "w") as f:
            json.dump(defaults, f, indent=4)
        return defaults
    try:
        with open(SETTINGS_FILE, "r") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return defaults
            merged = defaults.copy()
            merged.update(data)
            return merged
    except Exception:
        return defaults

def save_bot_settings(cfg):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(cfg, f, indent=4)

def get_numbers_per_pick():
    """How many numbers one tap of GET NUMBER hands out — admin-set, UNLIMITED (1 … any)."""
    try:
        return max(1, int(load_bot_settings().get("numbers_per_pick", 1)))
    except Exception:
        return 1

def set_numbers_per_pick(n):
    cfg = load_bot_settings()
    cfg["numbers_per_pick"] = max(1, int(n))
    save_bot_settings(cfg)

# ==================== DATA LOADER HELPER FUNCTIONS ====================

def load_data(filename=USER_DATA_FILE):
    if not os.path.exists(filename):
        with open(filename, "w") as f:
            json.dump({}, f)
        return {}
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_data(data, filename=USER_DATA_FILE):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)

def get_user(uid):
    uid = str(uid)
    data = load_data()
    if uid not in data:
        data[uid] = {
            "user_id": uid, "balance": 0.0, "total_numbers": 0,
            "referral_count": 0, "lang": None, "verified": False
        }
        save_data(data)
    else:
        modified = False
        if "lang" not in data[uid]:
            data[uid]["lang"] = None
            modified = True
        if "verified" not in data[uid]:
            data[uid]["verified"] = False
            modified = True
        if "balance" not in data[uid]:
            data[uid]["balance"] = 0.0
            modified = True
        if modified:
            save_data(data)
    return data[uid]

def update_user_profile_info(uid, full_name, username):
    uid = str(uid)
    data = load_data()
    if uid in data:
        data[uid]["full_name"] = full_name
        data[uid]["username"] = username or ""
        save_data(data)

def set_user_lang(uid, lang):
    uid = str(uid)
    data = load_data()
    if uid in data:
        data[uid]["lang"] = lang
        save_data(data)

def get_user_lang(uid):
    # UI is English-only (screenshot design); Bengali strings are kept only as legacy routing aliases.
    return "en"

def set_user_verified(uid, verified=True):
    uid = str(uid)
    data = load_data()
    if uid in data:
        data[uid]["verified"] = verified
        save_data(data)

def is_user_verified(uid):
    user = get_user(uid)
    return user.get("verified", False)

async def update_db_balance(uid, amount):
    uid = str(uid)
    data = load_data()
    if uid in data:
        data[uid]["balance"] = round(data[uid].get("balance", 0.0) + amount, 2)
        save_data(data)
        return data[uid]["balance"]
    return 0.0

def get_all_users():
    data = load_data(USER_DATA_FILE)
    return list(data.keys()) if data else []

def user_exists(uid):
    data = load_data(USER_DATA_FILE)
    return str(uid) in data

# ==================== WITHDRAW / BANNED / STATS DATABASE ====================

def load_withdraw_requests():
    return load_data(WITHDRAW_DATA_FILE)

def save_withdraw_requests(data):
    save_data(data, WITHDRAW_DATA_FILE)

def generate_payment_id():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=20))

def load_banned_users():
    if not os.path.exists(BANNED_USERS_FILE):
        with open(BANNED_USERS_FILE, "w") as f:
            json.dump([], f)
        return []
    try:
        with open(BANNED_USERS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def save_banned_users(banned_list):
    save_data(banned_list, BANNED_USERS_FILE)

def is_user_banned(uid):
    banned_list = load_banned_users()
    return str(uid) in banned_list

def ban_user(uid):
    banned_list = load_banned_users()
    uid_str = str(uid)
    if uid_str not in banned_list:
        banned_list.append(uid_str)
        save_banned_users(banned_list)
        return True
    return False

def unban_user(uid):
    banned_list = load_banned_users()
    uid_str = str(uid)
    if uid_str in banned_list:
        banned_list.remove(uid_str)
        save_banned_users(banned_list)
        return True
    return False

def load_referral_data():
    return load_data(REFERRAL_DATA_FILE)

def save_referral_data(data):
    save_data(data, REFERRAL_DATA_FILE)

def update_referral_count(uid, count):
    referral_data = load_referral_data()
    uid_str = str(uid)
    if uid_str not in referral_data:
        referral_data[uid_str] = {"referral_count": 0}
    referral_data[uid_str]["referral_count"] = count
    save_referral_data(referral_data)

def get_referral_count(uid):
    referral_data = load_referral_data()
    uid_str = str(uid)
    return referral_data.get(uid_str, {}).get("referral_count", 0)

def load_range_db():
    return load_data(DATA_RANGE_FILE)

def save_range_db(data):
    save_data(data, DATA_RANGE_FILE)

def save_number_range_info(uid, number, range_text):
    range_stat_bump(range_text, "alloc")
    _RELEASED.discard(normalize_number(number))
    db = load_range_db()
    flag, name = get_country_info(number)
    db[normalize_number(number)] = {
        "user_id": str(uid),
        "number": f"+{normalize_number(number)}",
        "range": range_text,
        "country": f"{flag} {name}",
        "ts": datetime.now().timestamp(),
    }
    save_range_db(db)

def restore_active_numbers(max_age_sec=3600):
    """After a restart, keep watching numbers allocated in the last hour (so their OTPs still get credited)."""
    try:
        db = load_range_db()
    except Exception:
        return 0
    now = datetime.now().timestamp()
    n = 0
    for num, info in db.items():
        ts = info.get("ts")
        if not ts or now - float(ts) > max_age_sec or num in active_numbers:
            continue
        try:
            active_numbers[num] = {"uid": int(info["user_id"]), "range": info.get("range", ""),
                                   "timestamp": datetime.fromtimestamp(float(ts)), "panel": "yesms"}
            n += 1
        except Exception:
            pass
    if n:
        print(f"♻️ restored {n} active number(s) from the last hour")
    return n

# ==================== COUNTRY MAPPING SECTION ====================

COUNTRY_MAP = {
    "2376": ("🇨🇲", "Cameroon"), "2250": ("🇨🇮", "Ivory Coast"), "2613": ("🇲🇬", "Madagascar"), "4077": ("🇷🇴", "Romania"),
    "237": ("🇨🇲", "Cameroon"), "225": ("🇨🇮", "Ivory Coast"), "261": ("🇲🇬", "Madagascar"), "20": ("🇪🇬", "Egypt"),
    "27": ("🇿🇦", "South Africa"), "234": ("🇳🇬", "Nigeria"), "254": ("🇰🇪", "Kenya"), "233": ("🇬🇭", "Ghana"),
    "212": ("🇲🇦", "Morocco"), "213": ("🇩🇿", "Algeria"), "216": ("🇹🇳", "Tunisia"), "218": ("🇱🇾", "Libya"),
    "249": ("🇸🇩", "Sudan"), "251": ("🇪🇹", "Ethiopia"), "252": ("🇸🇴", "Somalia"), "253": ("🇩🇯", "Djibouti"),
    "255": ("🇹🇿", "Tanzania"), "256": ("🇺🇬", "Uganda"), "257": ("🇧🇮", "Burundi"), "258": ("🇲🇿", "Mozambique"),
    "260": ("🇿🇲", "Zambia"), "263": ("🇿🇼", "Zimbabwe"), "264": ("🇳🇦", "Namibia"), "265": ("🇲🇼", "Malawi"),
    "266": ("🇱🇸", "Lesotho"), "267": ("🇧🇼", "Botswana"), "268": ("🇸🇿", "Swaziland"), "269": ("🇰🇲", "Comoros"),
    "220": ("🇬🇲", "Gambia"), "221": ("🇸🇳", "Senegal"), "222": ("🇲🇷", "Mauritania"), "223": ("🇲🇱", "Mali"),
    "224": ("🇬🇳", "Guinea"), "226": ("🇧🇫", "Burkina Faso"), "227": ("🇳🇪", "Niger"), "228": ("🇹🇬", "Togo"),
    "229": ("🇧🇯", "Benin"), "230": ("🇲🇺", "Mauritius"), "231": ("🇱🇷", "Liberia"), "232": ("🇸🇱", "Sierra Leone"),
    "235": ("🇹🇩", "Chad"), "236": ("🇨🇫", "Central African Republic"), "238": ("🇨🇻", "Cape Verde"),
    "239": ("🇸🇹", "Sao Tome and Principe"), "240": ("🇬🇶", "Equatorial Guinea"), "241": ("🇬🇦", "Gabon"),
    "242": ("🇨🇬", "Congo"), "243": ("🇨🇩", "DR Congo"), "244": ("🇦🇴", "Angola"), "245": ("🇬🇼", "Guinea-Bissau"),
    "247": ("🇸🇭", "Saint Helena"), "248": ("🇸🇨", "Seychelles"), "250": ("🇷🇼", "Rwanda"), "290": ("🇸🇭", "Saint Helena"),
    "291": ("🇪🇷", "Eritrea"), "40": ("🇷🇴", "Romania"), "44": ("🇬🇧", "United Kingdom"), "33": ("🇫🇷", "France"),
    "49": ("🇩🇪", "Germany"), "39": ("🇮🇹", "Italy"), "34": ("🇪🇸", "Spain"), "31": ("🇳🇱", "Netherlands"),
    "32": ("🇧🇪", "Belgium"), "41": ("🇨🇭", "Switzerland"), "43": ("🇦🇹", "Austria"), "46": ("🇸🇪", "Sweden"),
    "47": ("🇳🇴", "Norway"), "45": ("🇩🇰", "Denmark"), "358": ("🇫🇮", "Finland"), "351": ("🇵🇹", "Portugal"),
    "353": ("🇮🇪", "Ireland"), "36": ("🇭🇺", "Hungary"), "48": ("🇵🇱", "Poland"), "380": ("🇺🇦", "Ukraine"),
    "370": ("🇱🇹", "Lithuania"), "371": ("🇱🇻", "Latvia"), "372": ("🇪🇪", "Estonia"), "373": ("🇲🇩", "Moldova"),
    "374": ("🇦🇲", "Armenia"), "375": ("🇧🇾", "Belarus"), "376": ("🇦🇩", "Andorra"), "377": ("🇲🇨", "Monaco"),
    "381": ("🇷🇸", "Serbia"), "382": ("🇲🇪", "Montenegro"), "385": ("🇭🇷", "Croatia"), "386": ("🇸🇮", "Slovenia"),
    "387": ("🇧🇦", "Bosnia and Herzegovina"), "389": ("🇲🇰", "North Macedonia"), "350": ("🇬🇮", "Gibraltar"),
    "352": ("🇱🇺", "Luxembourg"), "354": ("🇮🇸", "Iceland"), "355": ("🇦🇱", "Albania"), "356": ("🇲🇹", "Malta"),
    "357": ("🇨🇾", "Cyprus"), "359": ("🇧🇬", "Bulgaria"), "421": ("🇸🇰", "Slovakia"), "420": ("🇨🇿", "Czech Republic"),
    "298": ("🇫🇴", "Faroe Islands"), "299": ("🇬🇱", "Greenland"), "1": ("🇺🇸", "United States"), "7": ("🇷🇺", "Russia"),
    "91": ("🇮🇳", "India"), "92": ("🇵🇰", "Pakistan"), "880": ("🇧🇩", "Bangladesh"), "86": ("🇨🇳", "China"),
    "81": ("🇯🇵", "Japan"), "82": ("🇰🇷", "South Korea"), "84": ("🇻🇳", "Vietnam"), "66": ("🇹🇭", "Thailand"),
    "62": ("🇮🇩", "Indonesia"), "60": ("🇲🇾", "Malaysia"), "65": ("🇸🇬", "Singapore"), "63": ("🇵🇭", "Philippines"),
    "95": ("🇲🇲", "Myanmar"), "94": ("🇱🇰", "Sri Lanka"), "977": ("🇳🇵", "Nepal"), "93": ("🇦🇫", "Afghanistan"),
    "98": ("🇮🇷", "Iran"), "90": ("🇹🇷", "Turkey"), "964": ("🇮🇶", "Iraq"), "963": ("🇸🇾", "Syria"),
    "961": ("🇱🇧", "Lebanon"), "962": ("🇯🇴", "Jordan"), "965": ("🇰🇼", "Kuwait"), "966": ("🇸🇦", "Saudi Arabia"),
    "967": ("🇾🇪", "Yemen"), "968": ("🇴🇲", "Oman"), "971": ("🇦🇪", "United Arab Emirates"), "972": ("🇮🇱", "Israel"),
    "973": ("🇧🇭", "Bahrain"), "974": ("🇶🇦", "Qatar"), "994": ("🇦🇿", "Azerbaijan"), "995": ("🇬🇪", "Georgia"),
    "996": ("🇰🇬", "Kyrgyzstan"), "992": ("🇹🇯", "Tajikistan"), "993": ("🇹🇲", "Turkmenistan"), "998": ("🇺🇿", "Uzbekistan"),
    "855": ("🇰🇭", "Cambodia"), "856": ("🇱🇦", "Laos"), "976": ("🇲🇳", "Mongolia"), "850": ("🇰🇵", "North Korea"),
    "55": ("🇧🇷", "Brazil"), "52": ("🇲🇽", "Mexico"), "54": ("🇦🇷", "Argentina"), "57": ("🇨🇴", "Colombia"),
    "51": ("🇵🇪", "Peru"), "58": ("🇻🇪", "Venezuela"), "56": ("🇨🇱", "Chile"), "593": ("🇪🇨", "Ecuador"),
    "591": ("🇧🇴", "Bolivia"), "595": ("🇵🇾", "Paraguay"), "598": ("🇺🇾", "Uruguay"), "502": ("🇬🇹", "Guatemala"),
    "503": ("🇸🇻", "El Salvador"), "504": ("🇭🇳", "Honduras"), "506": ("🇨🇷", "Costa Rica"), "507": ("🇵🇦", "Panama"),
    "509": ("🇭🇹", "Haiti"), "501": ("🇧🇿", "Belize"), "61": ("🇦🇺", "Australia"), "64": ("🇳🇿", "New Zealand"),
    "675": ("🇵🇬", "Papua New Guinea"), "679": ("🇫🇯", "Fiji"), "1246": ("🇧🇧", "Barbados"), "1876": ("🇯🇲", "Jamaica"),
    "53": ("🇨🇺", "Cuba"), "592": ("🇬🇾", "Guyana"),
}
_SORTED_PREFIXES = sorted(COUNTRY_MAP.keys(), key=len, reverse=True)

def get_country_info(number):
    clean_num = str(number).replace('+', '').replace(' ', '').replace('-', '').strip()
    for prefix in _SORTED_PREFIXES:
        if clean_num.startswith(prefix):
            return COUNTRY_MAP[prefix]
    return ("🌍", "Unknown")

def detect_service(full_sms):
    if not full_sms:
        return "SMS SERVICE"
    sms_lower = full_sms.lower()
    service_keywords = {
        "facebook": "FACEBOOK", "fb": "FACEBOOK", "instagram": "INSTAGRAM", "insta": "INSTAGRAM",
        "tiktok": "TIKTOK", "twitter": "TWITTER", "x.com": "TWITTER", "snapchat": "SNAPCHAT", "snap": "SNAPCHAT",
        "whatsapp": "WHATSAPP", "telegram": "TELEGRAM", "discord": "DISCORD", "messenger": "MESSENGER",
        "linkedin": "LINKEDIN", "google": "GOOGLE", "gmail": "GOOGLE", "amazon": "AMAZON", "microsoft": "MICROSOFT",
        "outlook": "MICROSOFT", "yahoo": "YAHOO", "paypal": "PAYPAL", "binance": "BINANCE", "coinbase": "COINBASE",
        "spotify": "SPOTIFY", "netflix": "NETFLIX", "uber": "UBER", "apple": "APPLE", "icloud": "APPLE",
        "bkash": "BKASH", "nagad": "NAGAD", "stripe": "STRIPE", "line": "LINE", "wechat": "WECHAT",
        "viber": "VIBER", "signal": "SIGNAL", "pubg": "PUBG", "free fire": "FREE FIRE",
    }
    for keyword, service_name in sorted(service_keywords.items(), key=lambda x: len(x[0]), reverse=True):
        if keyword in sms_lower:
            return service_name
    return "SMS SERVICE"

# ==================== SERVICE LOGO ENGINE ====================
# Brand-coloured "logo" emoji for every popular service. Used on the
# service-picker buttons, the admin service panel and all OTP cards.
# Keys are matched as substrings (longest first) so "WhatsApp Business"
# still resolves to the WhatsApp logo.
SERVICE_ICONS = {
    "whatsapp": "🟢", "wa": "🟢", "telegram": "✈️", "tg": "✈️", "facebook": "🔵", "fb": "🔵",
    "instagram": "📸", "insta": "📸", "ig": "📸", "tiktok": "🎵", "twitter": "🐦", "x.com": "✖️",
    "snapchat": "👻", "snap": "👻", "discord": "👾", "messenger": "💬", "linkedin": "💼",
    "google": "🅶", "gmail": "📧", "youtube": "▶️", "amazon": "🛒", "microsoft": "🪟", "outlook": "📨",
    "yahoo": "🟣", "paypal": "💙", "binance": "🟡", "coinbase": "🔷", "bybit": "🟠", "okx": "⚫",
    "kucoin": "🟢", "spotify": "🎧", "netflix": "🎬", "uber": "🚗", "apple": "🍎", "icloud": "☁️",
    "bkash": "💗", "nagad": "🧡", "rocket": "💜", "upay": "💛", "stripe": "💳", "line": "🟩",
    "wechat": "💚", "viber": "💜", "signal": "🔵", "imo": "🔷", "pubg": "🎮", "free fire": "🔥",
    "freefire": "🔥", "steam": "🎮", "roblox": "🧱", "openai": "🤖", "chatgpt": "🤖", "claude": "🧠",
    "tinder": "❤️‍🔥", "bumble": "🐝", "badoo": "💜", "shopee": "🧡", "lazada": "🛍️", "alibaba": "🟠",
    "aliexpress": "🛒", "daraz": "🟠", "foodpanda": "🐼", "pathao": "🛵", "grab": "🟢", "bolt": "⚡",
    "airbnb": "🏠", "booking": "🏨", "ebay": "🛒", "walmart": "🔵", "noon": "💛", "careem": "🟢",
    "zalo": "🔵", "kakao": "💛", "naver": "🟢", "mercari": "🔴", "payoneer": "🟠", "wise": "💚",
    "skrill": "💜", "neteller": "💚", "revolut": "⚫", "cashapp": "💵", "venmo": "🔵",
    "twitch": "🟣", "reddit": "🟠", "pinterest": "📌", "threads": "🧵", "quora": "🔴", "tumblr": "🔵",
    "zoom": "🎥", "skype": "🔷", "slack": "🟪", "notion": "📓", "canva": "🎨", "figma": "🎨",
    "github": "🐙", "twilio": "🔴", "nike": "✔️", "adidas": "⚫", "ikea": "🟦", "mcdonald": "🟨",
    "sms": "💬", "otp": "💬", "others": "⚡", "other": "⚡",
}
_SERVICE_ICON_KEYS = sorted(SERVICE_ICONS.keys(), key=len, reverse=True)

def service_icon(name, default="⚡"):
    """Return the best-matching brand emoji for a service name."""
    low = re.sub(r'[^a-z0-9. ]', ' ', str(name or "").lower())
    for k in _SERVICE_ICON_KEYS:
        if len(k) <= 2:
            # very short keys (wa / fb / ig / tg) must match as whole word
            if re.search(rf'(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])', low):
                return SERVICE_ICONS[k]
        elif k in low:
            return SERVICE_ICONS[k]
    return default

def service_label(name, accent=""):
    """'WhatsApp' -> '🟢 WhatsApp'  (logo + name, ready for a button)."""
    name = str(name or "").strip()
    return f"{service_icon(name)} {name}" + (f" {accent}" if accent else "")

def get_service_logo(service_name):
    service_upper = str(service_name).upper()
    icon = service_icon(service_upper, default="📩")
    return f"{icon} {service_upper}"

# ==================== PREMIUM CARD BUILDERS ====================

def welcome_card(lang, name=""):
    name = html.escape(name or ("বস" if lang == "bn" else "Legend"))
    if lang == "bn":
        return (
            f"{BRAND_LINE}\n{DIV}\n\n"
            f"🎊 <b>স্বাগতম, {name}!</b> 🎊\n"
            f"<i>আপনার নোড প্রিমিয়াম OTP নেটওয়ার্কে সংযুক্ত হয়েছে</i>\n\n"
            f"<blockquote>🟢 সার্ভার     ▸ <code>অনলাইন ✓</code>\n"
            f"⚡ ইঞ্জিন      ▸ <code>চলমান ✓</code>\n"
            f"🛡️ সিকিউরিটি  ▸ <code>ভেরিফাইড ✓</code>\n"
            f"🎁 রিওয়ার্ড  ▸ <code>সক্রিয় ✓</code></blockquote>\n\n"
            f"💠 <b>নিচের প্রিমিয়াম মেনু থেকে সার্ভিস সিলেক্ট করুন 👇</b>"
        )
    return (
        f"{BRAND_LINE}\n{DIV}\n\n"
        f"🎊 <b>WELCOME, {name.upper()}!</b> 🎊\n"
        f"<i>Your node is now linked to the Premium OTP Network.</i>\n\n"
        f"<blockquote>🟢 Server   ▸ <code>ONLINE ✓</code>\n"
        f"⚡ Engine   ▸ <code>ACTIVE ✓</code>\n"
        f"🛡️ Shield   ▸ <code>VERIFIED ✓</code>\n"
        f"🎁 Rewards  ▸ <code>ENABLED ✓</code></blockquote>\n\n"
        f"💠 <b>Pick a premium module below 👇</b>"
    )

def count_active_numbers(uid):
    return sum(1 for d in active_numbers.values() if str(d.get("uid")) == str(uid))

def profile_card(lang, uid, full_name, username, balance, stats):
    """MY PROFILE — identical structure to the reference UI."""
    return (
        f"{boxed('MY PROFILE', '👤')}"
        f"💳 <b>MY WALLET</b>\n\n"
        f"👤 <b>User ID:</b> <code>{uid}</code>\n"
        f"💬 <b>Today OTP:</b> {stats['today_otps']}\n"
        f"💬 <b>All OTP:</b> {stats['total_otps']}\n"
        f"📱 <b>Active Numbers:</b> {count_active_numbers(uid)}\n"
        f"🎁 <b>Total Referrals:</b> {get_referral_count(uid)}\n"
        f"💳 <b>Wallet Balance:</b> {format_balance(balance)} ৳\n"
        f"🛡️ <b>Minimum Withdrawal:</b> {MIN_WITHDRAW:.1f} ৳\n\n"
        f"<i>আপনার OTP, referral এবং wallet-এর সব তথ্য এখানে দেখা যাবে।</i>"
    )

def balance_card(lang, balance):
    if lang == "bn":
        return (
            f"{head('WALLET VAULT', '💰')}"
            f"💵 মোট ব্যালেন্স\n<code>{format_balance(balance)} BDT</code>\n\n"
            f"<blockquote>📉 সর্বনিম্ন উইথড্র ▸ <code>{MIN_WITHDRAW} BDT</code>\n"
            f"📈 সর্বোচ্চ উইথড্র ▸ <code>{MAX_WITHDRAW} BDT</code>\n"
            f"⏱️ পেআউট সময়       ▸ <code>0-24 ঘণ্টা</code></blockquote>\n\n"
            f"👇 ক্যাশআউট করতে উইথড্র বাটন চাপুন"
        )
    return (
        f"{head('WALLET VAULT', '💰')}"
        f"💵 Liquid Balance\n<code>{format_balance(balance)} BDT</code>\n\n"
        f"<blockquote>📉 Min Withdraw ▸ <code>{MIN_WITHDRAW} BDT</code>\n"
        f"📈 Max Withdraw ▸ <code>{MAX_WITHDRAW} BDT</code>\n"
        f"⏱️ Payout Time  ▸ <code>0-24 Hours</code></blockquote>\n\n"
        f"👇 Tap withdraw to cash out"
    )

def traffic_card(hours=1):
    """TRAFFIC — LAST 1 HOUR : OTPs on the whole network (auto-discovered console feed) + our own hits."""
    total, per_country, per_service = network_traffic(hours)
    since = datetime.now() - timedelta(hours=hours)
    mine = 0
    try:
        logs = load_data(ACTIVITY_LOGS_FILE) if os.path.exists(ACTIVITY_LOGS_FILE) else []
    except Exception:
        logs = []
    if isinstance(logs, list):
        for entry in reversed(logs):
            if entry.get("action") != "OTP_RECEIVED":
                continue
            try:
                ts = datetime.fromisoformat(entry.get("timestamp", ""))
            except Exception:
                continue
            if ts < since:
                break
            mine += 1
            if total == 0:                      # console offline → fall back to our own numbers
                num = (entry.get("details") or {}).get("number", "")
                flag, cname = get_country_info(num)
                k = f"{flag} {cname}"
                per_country[k] = per_country.get(k, 0) + 1
    shown_total = total or mine
    label = "LAST 1 HOUR" if hours == 1 else f"LAST {hours} HOURS"
    lines = [f"{head(f'TRAFFIC — {label}', '👑')}🔑 <b>OTP Received:</b> {shown_total}\n🌍 <b>Countries:</b>\n{DIV_S}"]
    if per_country:
        for k, v in sorted(per_country.items(), key=lambda x: -x[1])[:15]:
            flag, cname = _split_country(k)
            iso = flag_iso(flag)
            lines.append(f"{flag} {html.escape(cname)}{f' ({iso})' if iso else ''} — <b>{v}</b>")
    else:
        lines.append("<i>No OTP traffic in this window yet.</i>")
    # v16.11: View Range → live-traffic RANGES shown WITH their service
    since_ts = time.time() - hours * 3600
    per_range = {}
    for t, cty, svc, rid in _TRAFFIC_SEEN.values():
        if t < since_ts or not rid:
            continue
        key = (str(rid), str(svc or "Unknown"), str(cty or ""))
        per_range[key] = per_range.get(key, 0) + 1
    if per_range:
        lines.append(f"{DIV_S}\n🔥 <b>Live Ranges (with service):</b>")
        for (rid, svc, cty), n in sorted(per_range.items(), key=lambda x: -x[1])[:15]:
            flag, cname = _split_country(cty) if cty else ("🌍", "Unknown")
            lines.append(f"{service_icon(svc)} <b>{html.escape(svc.upper()[:12])}</b> · {flag} <code>{html.escape(rid)}</code> — <b>{n}</b>")
    if per_service:
        lines.append(f"{DIV_S}\n📱 <b>Services:</b> " + " · ".join(
            f"{service_icon(s)} {html.escape(str(s))} <b>{n}</b>" for s, n in sorted(per_service.items(), key=lambda x: -x[1])[:6]))
    if mine:
        lines.append(f"💎 <b>Our OTPs:</b> {mine}")
    lines.append(DIV)
    return "\n".join(lines)

def stock_card():
    """LIVE STOCK — services currently online with their country/range counts."""
    services = filter_visible_services(get_cached_services())
    out = [head('LIVE STOCK', '📁').rstrip("\n")]
    if not services:
        out.append("<i>No live services right now. Try Refresh in a moment.</i>")
    total = 0
    for svc in services:
        sid = str(svc.get("sid", "")).upper()
        ranges = svc.get("ranges", []) or []
        total += len(ranges)
        flags = []
        for r in ranges:
            f, _ = range_country(r)
            if f not in flags:
                flags.append(f)
        hot = sum(1 for r in ranges if (_RANGE_META.get(_norm_rid(r)) or {}).get("hot"))
        auto_n = int(svc.get("auto") or 0)
        badge = (f" 🔥{hot}" if hot else "") + (f" <i>(+{auto_n} auto)</i>" if auto_n else "")
        out.append(f"{service_icon(sid)} <b>{html.escape(sid)}</b> — {len(ranges)} range(s)  {''.join(flags[:10])}{badge}")
    all_c = {range_country(r)[1] for s in services for r in (s.get("ranges") or [])}
    out.append(f"{DIV_S}\n📦 <b>Total Ranges:</b> {total}  ·  🌍 <b>Countries:</b> {len(all_c)}\n"
               f"🤖 <i>Auto-discovery ON — new ranges/countries/services appear within seconds.</i>\n{DIV}\n<i>Use /start to get your numbers!</i>")
    return "\n".join(out)

def node_market_header(lang, multi=False):
    if multi:
        return f"📍 <b>Select a service:</b>  <i>(multi — up to {MULTI_MAX} numbers)</i>"
    return f"📍 <b>Select a service:</b>"

_LAST_ALLOC_ERROR = {"msg": ""}

def node_alloc_fail_card(lang):
    reason = _LAST_ALLOC_ERROR.get("msg") or ""
    if reason:
        nice = reason.rstrip("!.") 
        return (
            f"{head('ALLOCATION FAILED', '🚫')}"
            f"<b>{html.escape(nice)}</b>\n"
            f"<i>Try another country / range or retry in a moment.</i>"
        )
    if lang == "bn":
        return (
            f"{head('ALLOCATION FAILED', '🚫')}"
            f"<b>এই রেঞ্জে বর্তমানে কোনো নোড খালি নেই।</b>\n"
            f"<i>অন্য রেঞ্জ ট্রাই করুন অথবা কিছুক্ষণ পর আবার চেষ্টা করুন।</i>"
        )
    return (
        f"{head('ALLOCATION FAILED', '🚫')}"
        f"<b>No free nodes on this range right now.</b>\n"
        f"<i>Try another range or retry in a moment.</i>"
    )

def allocation_card(country_flag, country_name, range_text, service_logo, clean_num, instant=False, otp=None, sms=None):
    """Premium number-allocation card (single node)."""
    if instant and otp:
        otp_safe = html.escape(str(otp))
        sms_safe = html.escape(str(sms or ""))
        sms_block = f"\n\n📩 <b>PACKET</b>\n<blockquote expandable><code>{sms_safe}</code></blockquote>" if sms_safe else ""
        return (
            f"{head('INSTANT DECRYPT', '🔥')}"
            f"🌍 <b>Origin</b>  ▸ {country_flag} {html.escape(country_name)}\n"
            f"🛰️ <b>Range</b>   ▸ <code>{range_text}</code>\n"
            f"📱 <b>Service</b> ▸ {service_logo}\n\n"
            f"🔥 📟 <b>Number</b>  ▸ <code>+{clean_num}</code>\n"
            f"🔑 <b>OTP Key</b> ▸ <code>{otp_safe}</code>"
            f"{sms_block}\n\n"
            f"🟢 <b>DECRYPTED SUCCESSFULLY</b>"
        )
    return (
        f"{head('NODE ALLOCATED', '🛰️')}"
        f"🌍 <b>Origin</b>  ▸ {country_flag} {html.escape(country_name)}\n"
        f"🛰️ <b>Range</b>   ▸ <code>{range_text}</code>\n"
        f"📱 <b>Service</b> ▸ {service_logo}\n\n"
        f"🔥 📟 <b>Number</b>  ▸ <code>+{clean_num}</code>\n\n"
        f"📡 <b>Auto-scan armed</b> — OTP আসতেই এখানে + গ্রুপে পৌঁছে যাবে ⚡"
    )


# ==================== WOW PACK (no Premium needed) ====================
# Telegram-native polish that works for EVERY bot, no custom emoji / Premium required:
#   • message effects  → 🎉 confetti on join, 🔥 on OTP, ❤️ on welcome  (private chats)
#   • reactions        → bot reacts ❤️ to /start, ⚡ while allocating a number
#   • copy buttons     → 📋 COPY NUMBER / COPY OTP / COPY LINK (one-tap clipboard)
#   • share button     → 📤 SHARE referral link via Telegram share sheet
FX = {
    "fire":  "5104841245755180586",   # 🔥
    "party": "5046509860389126442",   # 🎉
    "heart": "5159385139981059251",   # ❤️
    "like":  "5107584321108051014",   # 👍
}

async def send_fx(bot, chat_id, text, effect=None, **kw):
    """send_message with a message effect; falls back silently if unsupported/rejected."""
    kw.setdefault("parse_mode", "HTML")
    if effect and int(chat_id) > 0:
        try:
            return await bot.send_message(chat_id=chat_id, text=text, message_effect_id=FX.get(effect, effect), **kw)
        except Exception:
            pass
    return await bot.send_message(chat_id=chat_id, text=text, **kw)

async def react(bot, chat_id, message_id, emoji="❤️", big=False):
    """Bot reaction on a message — purely cosmetic, never raises. `big=True` = large animated pop."""
    if not ReactionTypeEmoji:
        return
    try:
        await bot.set_message_reaction(chat_id=chat_id, message_id=message_id,
                                       reaction=[ReactionTypeEmoji(emoji)], is_big=big)
    except Exception:
        pass


async def premium_pop(bot, msg, emojis=("😎", "🔥"), big=True, delay=0.08):
    """Premium attitude burst on any message — big emoji reactions popping one by one.
    Used on every main screen (OTP card, profile, traffic, number entry). Respects /anim off.
    v16.8 FAST: 0.08s between pops."""
    if not anim_enabled() or msg is None:
        return
    for emo in emojis:
        await react(bot, msg.chat_id, msg.message_id, emo, big=big)
        await asyncio.sleep(delay)

async def fire_sweep(bot, msg, uid, track=10, delay=0.05):
    """v16.5 button-press fire: a 🔥 travels across the front of the screen (with a fading trail),
    then the caller's own animation presents the result. Respects /anim off. Never raises.
    v16.8 FAST: 0.05s/frame (~0.5s for the full sweep)."""
    if msg is None:
        return
    try:
        if not anim_enabled():
            return
    except Exception:
        pass
    try:
        for i in range(track):
            row = ["▁"] * track
            row[i] = "🔥"
            if i >= 1:
                row[i - 1] = "▪"
            if i >= 2:
                row[i - 2] = "·"
            try:
                await msg.edit_text(f"🔥 <b>FIRE ON THE SCREEN</b>\n<code>{''.join(row)}</code>", parse_mode="HTML")
            except Exception:
                return
            await asyncio.sleep(delay)
    except Exception:
        pass

async def button_fire(bot, chat_id, uid, track=10, delay=0.05):
    """v16.5 menu-button fire: a temp line sweeps 🔥 across the screen, then vanishes —
    the result is presented right after by the handler's own (best) animation. Respects /anim off.
    v16.8 FAST: 0.05s/frame."""
    try:
        if not anim_enabled():
            return
    except Exception:
        pass
    tmp = None
    try:
        tmp = await bot.send_message(chat_id=chat_id, text="<code>🔥▁▁▁▁▁▁▁▁▁</code>", parse_mode="HTML")
        await fire_sweep(bot, tmp, uid, track=track, delay=delay)
    except Exception:
        pass
    finally:
        if tmp is not None:
            try:
                await tmp.delete()
            except Exception:
                pass

def copy_button(label, value, style=None):
    """Inline button that copies `value` to the clipboard (Bot API 7.11+). None if unsupported."""
    if not CopyTextButton or not value:
        return None
    try:
        return InlineKeyboardButton(text=label, copy_text=CopyTextButton(text=str(value)[:256]), style=style)
    except TypeError:
        return InlineKeyboardButton(text=label, copy_text=CopyTextButton(text=str(value)[:256]))
    except Exception:
        return None

def share_button(label, url, text=""):
    from urllib.parse import quote
    return make_inline_keyboard_button(label, url=f"https://t.me/share/url?url={quote(url, safe='')}&text={quote(text, safe='')}")

def flag_iso(flag):
    """🇨🇲 → 'CM' (regional-indicator decode); '' for non-flags."""
    try:
        letters = [chr(ord(c) - 0x1F1E6 + 65) for c in flag if 0x1F1E6 <= ord(c) <= 0x1F1FF]
        return "".join(letters) if len(letters) == 2 else ""
    except Exception:
        return ""

def number_actions_kb(lang, number=None, otp=None):
    """Card actions under an allocated number: copy → repeat → live feed."""
    s_ok, s_pri, s_dgr = _styles(lang)
    rows = []
    top = []
    cb = copy_button("🔥 📋 COPY NUMBER", f"+{str(number).lstrip('+')}") if number else None
    co = copy_button("🔑 COPY OTP", otp) if otp else None
    if cb: top.append(cb)
    if co: top.append(co)
    if top: rows.append(top)
    rows.append([make_inline_keyboard_button(BTN[lang]["repeat"], callback_data="same_range", style=s_ok)])
    if number and not otp:
        n = str(number).lstrip("+")
        rows.append([make_inline_keyboard_button("🔁 CHANGE NUMBER", callback_data=f"chg_{n}", style=s_pri),
                     make_inline_keyboard_button("🗑️ RELEASE", callback_data=f"rel_{n}", style=s_dgr)])
    rows.append([make_inline_keyboard_button(BTN[lang]["otp_feed"], url="https://t.me/numbergenfastbot", style=s_pri)])
    return InlineKeyboardMarkup(rows)


def _num_ts_key(d):
    t = d.get("timestamp") or d.get("ts") or 0
    try:
        return t.timestamp() if hasattr(t, "timestamp") else float(t)
    except Exception:
        return 0.0


def user_active_numbers(uid, limit=5):
    """The user's live numbers, newest first (for the NEW NUMBER screen copy buttons)."""
    rows = [(num, d) for num, d in active_numbers.items() if str(d.get("uid")) == str(uid)]
    rows.sort(key=lambda x: _num_ts_key(x[1]), reverse=True)
    return [num for num, _ in rows[:limit]]


def new_number_kb(uid, sid):
    """ms_fast-style NEW NUMBER screen: green service button, blue 📋 copy-number buttons,
    [Change Number] red + [OTP Group ↗] blue."""
    rows = [[make_inline_keyboard_button(str(sid).title(), callback_data="ms_again", style="success")]]
    for num in user_active_numbers(uid, 5):
        cb = copy_button(f"🔥 📋 +{num}", f"+{num}", style="primary")
        if cb:
            rows.append([cb])
    rows.append([
        make_inline_keyboard_button("Change Number", callback_data="ms_change", style="danger"),
        make_inline_keyboard_button("OTP Group ↗", url=OTP_GROUP_JOIN_LINK, style="primary"),
    ])
    return InlineKeyboardMarkup(rows)

# ==================== SAFE BOT BUTTON BUILDERS ====================

def make_keyboard_button(text, style=None):
    try:
        return KeyboardButton(text=text, style=style)
    except TypeError:
        return KeyboardButton(text=text)

def make_inline_keyboard_button(text, callback_data=None, url=None, style=None):
    try:
        if url:
            return InlineKeyboardButton(text=text, url=url, style=style)
        return InlineKeyboardButton(text=text, callback_data=callback_data, style=style)
    except TypeError:
        if url:
            return InlineKeyboardButton(text=text, url=url)
        return InlineKeyboardButton(text=text, callback_data=callback_data)


# ==================== PREMIUM (CUSTOM) EMOJI ENGINE ====================
# Telegram Bot API 9.4+: bots can show *custom emoji* (premium emoji) both inside
# message text  (<tg-emoji emoji-id="…">fallback</tg-emoji>)  and as the icon of a
# button (icon_custom_emoji_id) — as long as the bot owner has Telegram Premium
# (or the bot carries a Fragment username).
#
# How this engine works:
#   • premium_emoji.json keeps a map  plain-emoji  ->  custom_emoji_id
#   • PremiumBot (below) rewrites EVERY outgoing message/caption/keyboard on the fly:
#       - mapped emoji in HTML text  → <tg-emoji> (premium render, plain emoji fallback)
#       - button label "💎 ɢᴇᴛ ɴᴜᴍʙᴇʀ" → icon_custom_emoji_id=<id>, text="ɢᴇᴛ ɴᴜᴍʙᴇʀ"
#   • If Telegram rejects custom emoji (owner has no Premium, bad id…) the request is
#     retried once WITHOUT premium emoji and the engine auto-disables — the bot never breaks.
#   • Admin → SYSTEM CORE → 🎨 PREMIUM EMOJI opens the Emoji Studio to map icons
#     simply by pasting premium emoji into the chat.

_PE = {"enabled": True, "map": {}, "auto_disabled": None, "loaded": False}

def _pe_norm(e):
    """Normalise an emoji key (drop variation selectors) so 🛡️ and 🛡 are the same key."""
    return str(e or "").replace("\uFE0F", "").replace("\uFE0E", "").strip()

def pe_load():
    if _PE["loaded"]:
        return _PE
    data = {}
    if os.path.exists(PREMIUM_EMOJI_FILE):
        try:
            with open(PREMIUM_EMOJI_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}
    _PE["enabled"] = bool(data.get("enabled", True))
    _PE["map"] = {_pe_norm(k): str(v) for k, v in (data.get("map") or {}).items() if k and v}
    _PE["loaded"] = True
    _pe_rebuild_regex()
    return _PE

def pe_save():
    with open(PREMIUM_EMOJI_FILE, "w", encoding="utf-8") as f:
        json.dump({"enabled": _PE["enabled"], "map": _PE["map"]}, f, indent=4, ensure_ascii=False)
    _pe_rebuild_regex()

_PE_RX = {"rx": None}

def _pe_rebuild_regex():
    keys = sorted(_PE["map"].keys(), key=len, reverse=True)
    if not keys:
        _PE_RX["rx"] = None
        return
    _PE_RX["rx"] = re.compile("(" + "|".join(re.escape(k) for k in keys) + r")\uFE0F?")

def pe_active():
    pe_load()
    return _PE["enabled"] and bool(_PE["map"])

def pe_id(emoji):
    """custom_emoji_id for a plain emoji, or None."""
    if not pe_active():
        return None
    return _PE["map"].get(_pe_norm(emoji))

def pe_set(emoji, custom_id):
    pe_load()
    _PE["map"][_pe_norm(emoji)] = str(custom_id)
    pe_save()

def pe_remove(emoji):
    pe_load()
    _PE["map"].pop(_pe_norm(emoji), None)
    pe_save()

def pe_toggle(state=None):
    pe_load()
    _PE["enabled"] = (not _PE["enabled"]) if state is None else bool(state)
    if _PE["enabled"]:
        _PE["auto_disabled"] = None
    pe_save()
    return _PE["enabled"]

def E(emoji):
    """Premium emoji for message text: <tg-emoji> when mapped, plain emoji otherwise."""
    cid = pe_id(emoji)
    return f'<tg-emoji emoji-id="{cid}">{emoji}</tg-emoji>' if cid else emoji

# -- leading-emoji detection for button labels ---------------------------------
_EMOJI_ATOM = (
    "(?:[\U0001F1E6-\U0001F1FF]{2}"                                   # flags
    "|(?:[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF\u2190-\u21FF\u25A0-\u25FF"
    "\u3030\u303D\u3297\u3299\u00A9\u00AE\u2122\u2139\u24C2\u2716\u2714\u2705\u274C\u2795\u2796]"
    "[\uFE0F\u20E3]?(?:\u200D[\U0001F000-\U0001FAFF\u2600-\u27BF][\uFE0F]?)*))"
)
_LEAD_EMOJI_RX = re.compile(rf"^({_EMOJI_ATOM})\s+(.+)$", re.S)

def pe_split_label(label):
    """'💎 ɢᴇᴛ ɴᴜᴍʙᴇʀ ⚡' → ('💎', 'ɢᴇᴛ ɴᴜᴍʙᴇʀ ⚡')  or (None, label)."""
    m = _LEAD_EMOJI_RX.match(str(label or ""))
    if not m:
        return None, label
    return m.group(1), m.group(2)

def pe_plain(label):
    """Label text as Telegram sends it back when the button carries a custom icon."""
    icon, rest = pe_split_label(label)
    return rest if icon else label

_PLAIN_TO_LABEL = {}

def canon_label(text):
    """Map the icon-less text of a premium button back to its canonical label (for routing)."""
    if not _PLAIN_TO_LABEL:
        pool = list(BTN["en"].values()) + list(BTN["bn"].values())
        pool += [v for k, v in globals().items() if (k.startswith("A_") or k.startswith("W_")) and isinstance(v, str)]
        for lab in pool:
            pl = pe_plain(lab)
            if pl != lab:
                _PLAIN_TO_LABEL.setdefault(pl, lab)
    return _PLAIN_TO_LABEL.get(text, text)

# -- outgoing payload rewriting ---------------------------------------------------
_TAG_SPLIT_RX = re.compile(r"(<[^>]+>)")
_SKIP_TAGS = ("code", "pre", "tg-emoji")

def pe_html(text):
    """Replace mapped emoji in an HTML message with <tg-emoji> (skips <code>/<pre>/<tg-emoji> bodies)."""
    rx = _PE_RX["rx"]
    if not text or rx is None or not pe_active():
        return text
    out, depth = [], 0
    for part in _TAG_SPLIT_RX.split(text):
        if not part:
            continue
        if part.startswith("<"):
            name = re.match(r"</?\s*([a-zA-Z\-]+)", part)
            name = name.group(1).lower() if name else ""
            if name in _SKIP_TAGS:
                depth += -1 if part.startswith("</") else 1
                depth = max(depth, 0)
            out.append(part)
        elif depth > 0:
            out.append(part)
        else:
            out.append(rx.sub(lambda m: f'<tg-emoji emoji-id="{_PE["map"][m.group(1)]}">{m.group(0)}</tg-emoji>', part))
    return "".join(out)

def _pe_button(d):
    """dict of a button → dict with icon_custom_emoji_id injected (when mapped)."""
    if not isinstance(d, dict) or d.get("icon_custom_emoji_id"):
        return d
    icon, rest = pe_split_label(d.get("text", ""))
    if not icon:
        return d
    cid = pe_id(icon)
    if not cid:
        return d
    nd = dict(d)
    nd["text"] = rest
    nd["icon_custom_emoji_id"] = cid
    return nd

def pe_markup(markup):
    """ReplyKeyboardMarkup / InlineKeyboardMarkup (object or dict) → dict with premium icons."""
    if markup is None or not pe_active():
        return markup
    try:
        d = markup.to_dict() if hasattr(markup, "to_dict") else dict(markup)
    except Exception:
        return markup
    key = "inline_keyboard" if "inline_keyboard" in d else ("keyboard" if "keyboard" in d else None)
    if not key:
        return markup
    d = dict(d)
    d[key] = [[_pe_button(b if isinstance(b, dict) else {"text": str(b)}) for b in row] for row in d[key]]
    return d

_PE_TEXT_ENDPOINTS = {"sendMessage", "editMessageText"}
_PE_CAPTION_ENDPOINTS = {"sendPhoto", "sendVideo", "sendAnimation", "sendDocument", "sendAudio", "editMessageCaption"}
_PE_ERR_HINTS = ("custom emoji", "custom_emoji", "emoji", "button_icon", "premium", "icon")

def pe_transform(endpoint, data):
    """Return a premium-ized copy of the request data (or the same dict if nothing to do)."""
    if not pe_active():
        return data
    nd = dict(data)
    pm = str(nd.get("parse_mode") or "").upper()
    if pm == "HTML":
        if endpoint in _PE_TEXT_ENDPOINTS and isinstance(nd.get("text"), str):
            nd["text"] = pe_html(nd["text"])
        if endpoint in _PE_CAPTION_ENDPOINTS and isinstance(nd.get("caption"), str):
            nd["caption"] = pe_html(nd["caption"])
    if "reply_markup" in nd:
        nd["reply_markup"] = pe_markup(nd["reply_markup"])
    return nd

# ==================== SMOOTH ANIMATION ENGINE (v14) ====================
# Every button tap plays a short, fresh transition before the real screen appears:
#   • reply-keyboard taps (GET NUMBER / TRAFFIC / PROFILE / SUPPORT) → a loader card that
#     fills up ▱▱▱ → ▰▰▰ and MORPHS into the real screen (same message, no flicker)
#   • inline button taps → 2-frame progress bar inside the same message, then the new screen
#   • long button lists (services / countries / ranges) cascade in row-by-row
#   • number allocation → live "radar" frames WHILE the panel is being asked (0 ms extra wait)
# Works on every Telegram client, no Premium needed. Admin: /anim on|off
import contextvars
_ANIM = contextvars.ContextVar("anim_state", default=None)
ANIM_FRAME_DELAY = 0.10          # v16.8 FAST: snappier tap loaders
ANIM_CASCADE_MIN_ROWS = 4

def anim_enabled():
    try:
        return bool(load_bot_settings().get("anim", True))
    except Exception:
        return True

def _bar(p, n=10):
    k = max(0, min(n, round(p * n)))
    return "▰" * k + "▱" * (n - k)

TAP_ANIM = {   # reply-keyboard button → (icon, title, [step texts])
    "get":     ("📞", "GET NUMBER", ["⚡ connecting to live panels…", "🛰️ syncing services + 🔥 traffic…", "✅ ready"]),
    "traffic": ("📊", "TRAFFIC",    ["📡 scanning the network…", "🔥 counting live OTPs…", "✅ ready"]),
    "profile": ("👤", "MY PROFILE", ["🔐 opening your vault…", "💳 loading wallet…", "✅ ready"]),
    "support": ("👥", "SUPPORT",    ["📞 connecting to support…", "👨‍💻 finding the admin…", "✅ ready"]),
}

def _cb_label(data):
    d = str(data or "")
    table = [("svc_", "🌍 loading countries"), ("cty_", "🛰️ loading live ranges"), ("back_services", "📍 loading services"),
             ("profile_refresh", "⚙️ refreshing profile"), ("stock_refresh", "📁 scanning stock"), ("withdraw", "💳 opening wallet"),
             ("open_refer", "🎁 building your link"), ("my_ref_", "🎁 loading referrals"), ("my_numbers", "📟 loading your numbers"),
             ("daily_bonus", "🎁 opening your gift"), ("pnl_", "🛰️ syncing panels"), ("verify_join", "🔍 verifying membership"),
             ("npk_", "🔢 updating"), ("svctl_", "🎛️ updating services"), ("full_logs_", "📜 loading logs"), ("pe_", "🎨 loading studio"),
             ("custom_range", "🛠 opening range tool"), ("admin_", "🛡️ processing")]
    for k, v in table:
        if d.startswith(k):
            return v
    return "⚡ loading"

ANIM_SKIP_CB = ("rng_", "rauto_", "quick_pick", "same_range", "chg_", "rel_", "mqty_", "pnl_close", "ui_close", "close", "copy", "ms_again", "ms_change")

def _mk_rows(markup):
    """→ (rows, rebuild(rows)) for an inline markup given as object or dict; (None, None) otherwise."""
    if markup is None:
        return None, None
    if isinstance(markup, InlineKeyboardMarkup):
        rows = [list(r) for r in markup.inline_keyboard]
        return rows, (lambda rs: InlineKeyboardMarkup(rs))
    if isinstance(markup, dict) and "inline_keyboard" in markup:
        rows = [list(r) for r in markup["inline_keyboard"]]
        return rows, (lambda rs: {**markup, "inline_keyboard": rs})
    return None, None

def _is_inline_or_none(markup):
    return markup is None or isinstance(markup, InlineKeyboardMarkup) or (isinstance(markup, dict) and "inline_keyboard" in markup)

async def _anim_cascade(bot, endpoint, data, kwargs):
    """Final screen: long keyboards slide in (top half first, then everything)."""
    rows, rebuild = _mk_rows(data.get("reply_markup"))
    if rows and len(rows) >= ANIM_CASCADE_MIN_ROWS:
        try:
            part = dict(data); part["reply_markup"] = rebuild(rows[:max(1, (len(rows) + 1) // 2)])
            await bot._raw_post(endpoint, part, **kwargs)
            await asyncio.sleep(ANIM_FRAME_DELAY * 0.4)   # v16.8 FAST
        except Exception:
            pass
    return await bot._raw_post(endpoint, data, **kwargs)

async def anim_edit(bot, data, kwargs, st):
    """Inline tap: progress frames inside the same message, then the new screen."""
    label = st.get("label") or "⚡ loading"
    base = {k: data[k] for k in ("chat_id", "message_id", "inline_message_id") if k in data and data[k] is not None}
    try:   # v16.8 FAST: one quick loader frame, then the screen slides in
        await bot._raw_post("editMessageText", {**base, "parse_mode": "HTML",
                            "text": f"<b>{html.escape(label)}…</b>\n<code>{_bar(0.7)}</code> 70%"}, **kwargs)
        await asyncio.sleep(ANIM_FRAME_DELAY)
    except Exception:
        pass
    return await _anim_cascade(bot, "editMessageText", data, kwargs)

async def anim_send(bot, data, kwargs, st):
    """Reply-keyboard tap: loader card morphs into the real screen. Returns None to fall back to a plain send."""
    if not _is_inline_or_none(data.get("reply_markup")) or not data.get("chat_id"):
        return None
    icon, title, steps = TAP_ANIM.get(st.get("tap"), ("⚡", "LOADING", ["⚡ loading…", "⚡ almost there…", "✅ ready"]))
    def frame(i, p):
        return f"{icon} <b>{title}</b>\n<code>{_bar(p)}</code> <b>{int(p * 100)}%</b>\n<i>{steps[min(i, len(steps) - 1)]}</i>"
    first = {k: data[k] for k in ("chat_id", "message_thread_id", "reply_parameters", "message_effect_id", "business_connection_id") if data.get(k) is not None}
    first.update({"text": frame(0, 0.12), "parse_mode": "HTML"})
    try:
        msg = await bot._raw_post("sendMessage", first, **kwargs)
        mid = msg.get("message_id") if isinstance(msg, dict) else None
    except Exception:
        return None
    if not mid:
        return None
    try:   # v16.8 FAST: one quick frame, straight to the real screen
        await asyncio.sleep(ANIM_FRAME_DELAY)
        await bot._raw_post("editMessageText", {"chat_id": data["chat_id"], "message_id": mid, "parse_mode": "HTML", "text": frame(1, 1.0)}, **kwargs)
    except Exception:
        pass
    await asyncio.sleep(ANIM_FRAME_DELAY * 0.4)
    final = {k: v for k, v in data.items() if k not in ("reply_parameters", "message_effect_id", "message_thread_id", "protect_content",
                                                         "disable_notification", "allow_paid_broadcast", "reply_to_message_id",
                                                         "allow_sending_without_reply")}
    final["message_id"] = mid
    try:
        return await _anim_cascade(bot, "editMessageText", final, kwargs)
    except Exception as e:
        print(f"[anim] morph failed ({e}) — sending plain")
        try:
            await bot._raw_post("deleteMessage", {"chat_id": data["chat_id"], "message_id": mid}, **kwargs)
        except Exception:
            pass
        return None

class AnimTap:
    """with AnimTap('get'): …  → the first message the handler sends is animated."""
    def __init__(self, tap=None, label=None, kind="tap"):
        self.state = {"kind": kind, "tap": tap, "label": label, "used": False}
    def __enter__(self):
        # v16.9 SIMPLE: tap loader animations are OFF — buttons answer instantly with the result
        self.token = _ANIM.set(None)
        return self
    def __exit__(self, *a):
        _ANIM.reset(self.token)
        return False

class AllocRadar:
    """Radar frames on the allocation message while the panel is being asked — never delays the number."""
    STEPS = ["🛰️ scanning live range", "🔐 locking a fresh number", "📡 handshake with panel", "⚡ almost there"]
    def __init__(self, message, range_text):
        self.message, self.range_text = message, range_text
        self.done = asyncio.Event(); self.task = None
    async def _run(self):
        i = 0
        while not self.done.is_set() and i < 6:
            p = min(0.95, 0.2 + i * 0.17)
            try:
                await self.message.edit_text(
                    f"{self.STEPS[min(i, len(self.STEPS) - 1)]}…\n<code>{_bar(p)}</code> {int(p * 100)}%\n"
                    f"🛰️ <code>{html.escape(str(self.range_text))}</code>", parse_mode="HTML")
            except Exception:
                pass
            i += 1
            try:
                await asyncio.wait_for(self.done.wait(), timeout=0.45)
            except asyncio.TimeoutError:
                pass
    def start(self):
        if anim_enabled() and self.message is not None:
            self.task = asyncio.create_task(self._run())
        return self
    async def stop(self):
        self.done.set()
        if self.task:
            try:
                await asyncio.wait_for(self.task, timeout=2)
            except Exception:
                pass

async def anim_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    arg = (context.args[0].lower() if context.args else "")
    st = load_bot_settings()
    if arg in ("on", "off"):
        st["anim"] = arg == "on"
        save_bot_settings(st)
    on = bool(load_bot_settings().get("anim", True))
    await update.message.reply_text(f"{head('ANIMATIONS', '✨')}Status: <b>{'🟢 ON' if on else '🔴 OFF'}</b>\n<i>/anim on · /anim off</i>", parse_mode="HTML")

class PremiumBot(ExtBot):
    """ExtBot that paints premium emoji on every outgoing message & button,
    with a transparent plain-emoji fallback if Telegram refuses."""

    async def _do_post(self, endpoint, data, **kwargs):
        st = _ANIM.get()
        if st and not st.get("used"):
            if endpoint == "editMessageText" and st.get("kind") == "cb":
                st["used"] = True
                return await anim_edit(self, data, kwargs, st)
            if endpoint == "sendMessage" and st.get("kind") == "tap":
                st["used"] = True
                r = await anim_send(self, data, kwargs, st)
                if r is not None:
                    return r
        return await self._raw_post(endpoint, data, **kwargs)

    async def _raw_post(self, endpoint, data, **kwargs):
        if not pe_active():
            return await super()._do_post(endpoint, data, **kwargs)
        try:
            return await super()._do_post(endpoint, pe_transform(endpoint, data), **kwargs)
        except BadRequest as e:
            msg = str(e).lower()
            if any(h in msg for h in _PE_ERR_HINTS):
                _PE["enabled"] = False
                _PE["auto_disabled"] = str(e)
                print(f"⚠️ premium emoji auto-disabled → {e}")
                return await super()._do_post(endpoint, data, **kwargs)
            raise

async def pe_validate_ids(bot):
    """Drop custom-emoji ids Telegram does not recognise (keeps the engine crash-proof)."""
    pe_load()
    ids = sorted(set(_PE["map"].values()))
    if not ids:
        return 0, 0
    valid = set()
    for i in range(0, len(ids), 150):
        chunk = ids[i:i + 150]
        try:
            stickers = await bot.get_custom_emoji_stickers(chunk)
            valid.update(str(st.custom_emoji_id) for st in stickers)
        except Exception:
            for cid in chunk:
                try:
                    st = await bot.get_custom_emoji_stickers([cid])
                    valid.update(str(x.custom_emoji_id) for x in st)
                except Exception:
                    pass
    bad = [k for k, v in _PE["map"].items() if v not in valid]
    for k in bad:
        _PE["map"].pop(k, None)
    if bad:
        pe_save()
    return len(valid), len(bad)


# ---------- EMOJI CLONE (forward any premium-emoji message → icons copied by label) ----------
# The reference bot's icons are Telegram *custom emoji*. Their ids cannot be read from a
# screenshot, but they travel inside a forwarded message. So: the admin forwards the
# reference bot's messages (MY PROFILE / TRAFFIC / SUPPORT / join prompt …) to this bot and
# every icon is copied onto OUR matching icon, using the label next to it.
#   "<icon> Wallet Balance: 0.00 ৳"  →  our 💳  gets that icon
#   "<icon> User ID: 123"            →  our 👤  gets that icon  … etc.
_PE_CLONE_LABELS = [
    ("minimum withdraw", "🛡️"),
    ("wallet", "💳"), ("withdraw", "💳"), ("balance", "💳"),
    ("user id", "👤"), ("my profile", "👤"), ("profile", "👤"),
    ("otp received", "🔑"),
    ("today otp", "💬"), ("all otp", "💬"), ("otp", "💬"),
    ("active number", "📱"),
    ("referral", "🎁"), ("refer", "🎁"),
    ("countries", "🌍"), ("country", "🌍"),
    ("traffic", "👑"), ("admin", "👑"),
    ("support", "👨‍💻"),
    ("get number", "📞"), ("stock", "📁"), ("refresh", "⚙️"), ("close", "❌"),
    ("select a service", "📍"), ("select a country", "📍"),
    ("thanks for joining", "✅"), ("check joined", "✅"),
    ("join channel", "📢"), ("join group", "📢"), ("please join", "⚠️"),
    ("welcome", "👋"), ("new number", "🆕"), ("price", "💰"), ("rate", "💰"),
]

def _pe_label_target(after_text):
    t = re.sub(r"[^a-z ]+", " ", (after_text or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    for key, ours in _PE_CLONE_LABELS:
        if key in t:
            return ours
    return None

def pe_clone_pairs(msg):
    """[(our_plain_emoji, custom_id, fallback_emoji, label)] learned from one message."""
    text = msg.text or msg.caption or ""
    ents = (msg.entities or []) if msg.text else (msg.caption_entities or [])
    out = []
    # UTF-16 offsets → python indices
    u16 = text.encode("utf-16-le")
    def py_idx(off):
        return len(u16[:off * 2].decode("utf-16-le", errors="ignore"))
    for ent in ents:
        cid = getattr(ent, "custom_emoji_id", None)
        if ent.type != MessageEntity.CUSTOM_EMOJI or not cid:
            continue
        a, b = py_idx(ent.offset), py_idx(ent.offset + ent.length)
        fallback = _pe_norm(text[a:b])
        line_end = text.find("\n", b)
        after = text[b:line_end if line_end != -1 else len(text)]
        after = after.split(":")[0]  # label only ("Wallet Balance", not the value)
        target = _pe_label_target(after)
        out.append((target, str(cid), fallback, after.strip()[:30]))
    if not out and msg.sticker and getattr(msg.sticker, "custom_emoji_id", None):
        out.append((None, str(msg.sticker.custom_emoji_id), _pe_norm(msg.sticker.emoji or ""), ""))
    return out

def pe_clone_apply(pairs):
    """Fallback-emoji matches first, then label matches override → returns report lines."""
    lines = []
    for target, cid, fallback, label in pairs:
        if fallback and len(fallback) <= 8 and not re.search(r"[A-Za-z0-9]", fallback):
            pe_set(fallback, cid)
    for target, cid, fallback, label in pairs:
        if target:
            pe_set(target, cid)
            lines.append(f"{target} ◂ <b>{html.escape(label or fallback)}</b>  ✅")
        elif fallback:
            lines.append(f"{fallback} ◂ <i>same icon</i>  ✅")
        else:
            lines.append(f"❓ <code>{cid}</code> — Studio তে আইকন ট্যাপ করে দিন")
    return lines

def _is_forwarded(msg):
    return bool(getattr(msg, "forward_origin", None) or getattr(msg, "forward_date", None))

# Every plain emoji the UI uses — shown in the Studio so the admin can map each one.
def pe_ui_emojis():
    seen, out = set(), []
    def add(e):
        k = _pe_norm(e)
        if k and k not in seen:
            seen.add(k); out.append(e)
    for lang in ("en", "bn"):
        for lab in BTN[lang].values():
            ic, _ = pe_split_label(lab); ic and add(ic)
    for k, v in list(globals().items()):
        if (k.startswith("A_") or k.startswith("W_")) and isinstance(v, str):
            ic, _ = pe_split_label(v); ic and add(ic)
    for e in ["💎", "👑", "💰", "🏆", "🎁", "🛡️", "⚡", "🔑", "📱", "🌍", "🛰️", "📟", "📩", "🟢", "🔴",
              "✅", "❌", "🎉", "🔥", "📊", "🎯", "💸", "🆔", "🗝️", "🏷️", "📡", "🎧", "🌐", "🔐", "🎰",
              "🚫", "⏱️", "📉", "📈", "🎊", "✨", "🔵", "📸", "🎵", "🐦", "👻", "✈️", "👾", "💬", "💼",
              "🅶", "🛒", "🪟", "🟣", "💙", "🟡", "🔷", "🍎", "💗", "🧡", "💜", "🎮", "🤖", "🧩", "↩️"]:
        add(e)
    return out

def _styles(lang=None):
    """Coloured button styles — applied uniformly to EN & BN for a consistent premium look."""
    return ("success", "primary", "danger")

# Placeholder text shown inside the input field under each keyboard (smooth UX hint)
_PLACEHOLDER = {
    "en": {
        "main": "Message",
        "cancel": "✍️ Type here or tap ❌ to cancel",
        "withdraw": "💳 Choose your payout gateway…",
        "admin": "🛡️ Admin command center",
        "video": "🎬 Send videos, then tap ✅ DONE",
    },
    "bn": {
        "main": "💎 মেনু থেকে একটি অপশন বেছে নিন…",
        "cancel": "✍️ এখানে লিখুন অথবা ❌ বাতিল চাপুন",
        "withdraw": "💳 পেমেন্ট গেটওয়ে বেছে নিন…",
        "admin": "🛡️ অ্যাডমিন কমান্ড সেন্টার",
        "video": "🎬 ভিডিও পাঠান, তারপর ✅ DONE চাপুন",
    },
}

def _rk(rows, lang="en", key="main"):
    """Smooth ReplyKeyboard: persistent, auto-resized, with a contextual placeholder.
    Falls back gracefully on older PTB versions."""
    ph = _PLACEHOLDER.get(lang, _PLACEHOLDER["en"]).get(key, "")
    try:
        return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True, input_field_placeholder=ph)
    except TypeError:
        try:
            return ReplyKeyboardMarkup(rows, resize_keyboard=True, input_field_placeholder=ph)
        except TypeError:
            return ReplyKeyboardMarkup(rows, resize_keyboard=True)

# ==================== SYSTEM KEYBOARDS ====================

def main_keyboard(user_id):
    """v16.10 CUSTOM SQUARE MENU (reference screenshot): 2×2 grid of blue (primary) buttons
    + full-width [Switch V2]. All seven buttons stay; admin panel remains reachable via /admin."""
    lang = get_user_lang(user_id)
    B = BTN[lang]
    keyboard = [
        [make_keyboard_button(B["get_number_v2"], style="primary"), make_keyboard_button(B["view_range"], style="primary")],
        [make_keyboard_button(B["my_status"], style="primary"), make_keyboard_button(B["withdraw_v2"], style="primary")],
        [make_keyboard_button(B["refer_earn"], style="primary"), make_keyboard_button(B["support_admin"], style="primary")],
        [make_keyboard_button(B["switch_v2"], style="primary")],
    ]
    # v16.11: admin panel restored for ADMINS ONLY (regular users keep the exact screenshot menu)
    if is_admin(user_id):
        keyboard.append([make_keyboard_button(B["admin"], style="danger")])
    return _rk(keyboard, lang, "main")

def cancel_keyboard(user_id):
    lang = get_user_lang(user_id)
    s_ok, s_pri, s_dgr = _styles(lang)
    return _rk([[make_keyboard_button(BTN[lang]["cancel"], style=s_dgr)]], lang, "cancel")

def video_setup_keyboard(user_id):
    lang = get_user_lang(user_id)
    s_ok, s_pri, s_dgr = _styles(lang)
    return _rk([
        [make_keyboard_button(A_DONE, style=s_ok)],
        [make_keyboard_button(BTN[lang]["cancel"], style=s_dgr)]
    ], lang, "video")

def admin_main_keyboard(show_team=False):
    keyboard = [
        [make_keyboard_button(A_USER_CENTER, style="primary")],
        [make_keyboard_button(A_SYSTEM_CORE, style="primary")],
    ]
    if show_team:
        keyboard.append([make_keyboard_button(A_ADMIN_TEAM, style="success")])
    keyboard.append([make_keyboard_button(A_EXIT, style="danger")])
    return _rk(keyboard, "en", "admin")

def admin_team_keyboard():
    keyboard = [
        [make_keyboard_button(A_ADD_ADMIN, style="success"), make_keyboard_button(A_REMOVE_ADMIN, style="danger")],
        [make_keyboard_button(A_ADMIN_LIST, style="primary")],
        [make_keyboard_button(A_BACK, style="danger")]
    ]
    return _rk(keyboard, "en", "admin")

def user_management_keyboard():
    keyboard = [
        [make_keyboard_button(A_BROADCAST, style="success")],
        [make_keyboard_button(A_IDS, style="primary"), make_keyboard_button(A_BALANCES, style="primary")],
        [make_keyboard_button(A_BANLIST, style="primary")],
        [make_keyboard_button(A_BACK, style="danger")]
    ]
    return _rk(keyboard, "en", "admin")

def system_config_keyboard():
    keyboard = [
        [make_keyboard_button(A_STATS, style="primary"), make_keyboard_button(A_INSPECT, style="primary")],
        [make_keyboard_button(A_PAYOUTS, style="success")],
        [make_keyboard_button(A_SERVICES, style="primary"), make_keyboard_button(A_NUMPICK, style="primary")],
        [make_keyboard_button(A_VIDEOS, style="success"), make_keyboard_button(A_CLEAR_VIDEOS, style="danger")],
        [make_keyboard_button(A_INJECT, style="success"), make_keyboard_button(A_PURGE, style="danger")],
        [make_keyboard_button(A_BAN, style="danger"), make_keyboard_button(A_UNBAN, style="success")],
        [make_keyboard_button(A_BANLIST, style="primary"), make_keyboard_button(A_UPDATE_JOIN, style="primary")],
        [make_keyboard_button(A_PANELS, style="success"), make_keyboard_button(A_EMOJI, style="success")],
        [make_keyboard_button(A_PANEL_RATES, style="primary")],
        [make_keyboard_button(A_BACK, style="danger")]
    ]
    return _rk(keyboard, "en", "admin")

def withdraw_method_keyboard(user_id):
    lang = get_user_lang(user_id)
    s_ok, s_pri, s_dgr = _styles(lang)
    keyboard = _rk([
        [make_keyboard_button(W_BKASH, style=s_pri), make_keyboard_button(W_NAGAD, style=s_pri)],
        [make_keyboard_button(W_ROCKET, style=s_pri), make_keyboard_button(W_BINANCE, style=s_ok)],
        [make_keyboard_button(BTN[lang]["cancel"], style=s_dgr)]
    ], lang, "withdraw")
    return keyboard

# ==================== INLINE ACTION BARS (under cards) ====================

def close_button(lang="en"):
    return make_inline_keyboard_button(BTN["en"]["close"], callback_data="ui_close", style="danger")

def refresh_button(cb):
    return make_inline_keyboard_button(BTN["en"]["refresh"], callback_data=cb)

def profile_actions_kb(lang="en"):
    return InlineKeyboardMarkup([
        [make_inline_keyboard_button(BTN["en"]["withdraw"], callback_data="withdraw_start")],
        [make_inline_keyboard_button(BTN["en"]["refer"], callback_data="open_refer")],
        [make_inline_keyboard_button("📜 MY NUMBERS", callback_data="my_numbers", style="primary"),
         make_inline_keyboard_button("🎁 DAILY BONUS", callback_data="daily_bonus", style="success")],
        [refresh_button("profile_refresh"), close_button()],
    ])

def traffic_actions_kb():
    return InlineKeyboardMarkup([
        [make_inline_keyboard_button(BTN["en"]["stock"], callback_data="stock_view"), refresh_button("traffic_refresh")],
        [close_button()],
    ])

def stock_actions_kb():
    return InlineKeyboardMarkup([
        [make_inline_keyboard_button(BTN["en"]["traffic"], callback_data="traffic_view"), refresh_button("stock_refresh")],
        [close_button()],
    ])

# ==================== SMOOTH UX HELPERS (typing pulse + cinematic intro) ====================

async def _typing(context, chat_id, delay=0.0):
    """Show the 'typing…' indicator so transitions feel alive instead of abrupt."""
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    except Exception:
        pass
    if delay:
        await asyncio.sleep(delay)

async def _safe_edit(msg, text, reply_markup=None):
    """Edit a message in place; silently ignore 'not modified' / already-deleted errors."""
    try:
        await msg.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
        return True
    except Exception:
        return False

# ==================== EMOJI CAPTCHA VERIFICATION ====================

EMOJI_POOL = ['🍎', '🦊', '🚗', '🥑', '🍕', '⚽', '🎸', '🚀', '🐱', '🐼', '🍩', '🏀']

def generate_emoji_captcha():
    correct = random.sample(EMOJI_POOL, 3)
    correct_str = " ".join(correct)
    options = [correct_str]
    while len(options) < 4:
        wrong = random.sample(EMOJI_POOL, 3)
        wrong_str = " ".join(wrong)
        if wrong_str not in options:
            options.append(wrong_str)
    random.shuffle(options)
    return correct_str, options

async def send_verification_captcha(chat_id, context: ContextTypes.DEFAULT_TYPE, uid, edit_msg=None, retry=False):
    """Emoji captcha. If `edit_msg` is given the captcha morphs INTO that message
    (smooth, no chat spam). `retry=True` adds a gentle 'wrong match' banner."""
    correct_ans, options = generate_emoji_captcha()
    context.user_data["captcha_correct"] = correct_ans

    lang = get_user_lang(uid)
    retry_line = ""
    if retry:
        retry_line = ("❌ <b>ভুল মিল! নতুন সিকোয়েন্স দেওয়া হলো, আবার চেষ্টা করুন</b>\n\n"
                      if lang == "bn" else
                      "❌ <b>Wrong match! New sequence generated — try again</b>\n\n")
    if lang == "bn":
        text = (
            f"{head('SECURITY CHECK', '🛡️')}"
            f"{retry_line}"
            f"নিচের <b>৩-ইমোজি সিকোয়েন্সের</b> সাথে হুবহু মিলিয়ে সঠিক বাটনে ক্লিক করুন:\n\n"
            f"👉 <code>{correct_ans}</code>\n\n"
            f"<i>⏱️ একটি সঠিক মিলেই লাইফটাইম অ্যাক্সেস!</i>"
        )
    else:
        text = (
            f"{head('SECURITY CHECK', '🛡️')}"
            f"{retry_line}"
            f"Match the exact <b>3-emoji sequence</b> below by tapping the right button:\n\n"
            f"👉 <code>{correct_ans}</code>\n\n"
            f"<i>⏱️ One correct match grants lifetime access!</i>"
        )

    # 2×2 grid → compact, thumb-friendly, premium look
    btns = [make_inline_keyboard_button(opt, callback_data=f"captcha_opt_{i}", style="primary") for i, opt in enumerate(options)]
    buttons = [btns[0:2], btns[2:4]]

    context.user_data["captcha_options"] = options
    kb = InlineKeyboardMarkup(buttons)
    if edit_msg is not None and await _safe_edit(edit_msg, text, kb):
        return
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=kb)

# ==================== UTILITY & STATUS LOG FUNCTIONS ====================

def format_balance(balance):
    return f"{balance:.2f}"

def extract_otp(text):
    if not text or text == "No Content":
        return "N/A"
    spaced_otp = re.search(r'\b(\d{3}\s\d{3})\b', text)
    if spaced_otp:
        return spaced_otp.group(1).replace(" ", "")
    match = re.search(r'\b(\d{4,8})\b', text)
    return match.group(1) if match else "N/A"

def normalize_number(num):
    return re.sub(r'\D', '', str(num))

def mask_number(num):
    if len(num) > 6:
        return f"{num[:4]}****{num[-6:]}"
    return num

def get_date_reset_time():
    now = datetime.now()
    return datetime(now.year, now.month, now.day, 0, 0, 0)

def is_valid_bangladesh_number(number):
    number = re.sub(r'\D', '', str(number))
    return len(number) == 11 and number.startswith('01')

def is_range_request(param):
    return 'X' in param.upper()

def is_referral_request(param):
    return param.isdigit()

def load_stats():
    return load_data(STATS_FILE)

def save_stats(stats):
    save_data(stats, STATS_FILE)

def add_number_taken(uid, count=1):
    uid = str(uid)
    stats = load_stats()
    if uid not in stats:
        stats[uid] = {"numbers_taken": [], "otps_received": []}
    now = datetime.now().isoformat()
    for _ in range(count):
        stats[uid]["numbers_taken"].append(now)
    log_global_activity(uid, "NUMBER_TAKEN", {"count": count})
    save_stats(stats)

def add_otp_received(uid):
    uid = str(uid)
    stats = load_stats()
    if uid not in stats:
        stats[uid] = {"numbers_taken": [], "otps_received": []}
    stats[uid]["otps_received"].append(datetime.now().isoformat())
    save_stats(stats)

def get_user_stats(uid):
    uid = str(uid)
    stats = load_stats()
    user_stats = stats.get(uid, {"numbers_taken": [], "otps_received": []})
    now = datetime.now()
    today_midnight = get_date_reset_time()
    last_24h = now - timedelta(hours=24)
    last_7d = now - timedelta(days=7)
    numbers_taken = user_stats.get("numbers_taken", [])
    otps_received = user_stats.get("otps_received", [])
    today_numbers = sum(1 for t in numbers_taken if datetime.fromisoformat(t) >= today_midnight)
    today_otps = sum(1 for t in otps_received if datetime.fromisoformat(t) >= today_midnight)
    last24h_numbers = sum(1 for t in numbers_taken if datetime.fromisoformat(t) > last_24h)
    last24h_otps = sum(1 for t in otps_received if datetime.fromisoformat(t) > last_24h)
    last7d_numbers = sum(1 for t in numbers_taken if datetime.fromisoformat(t) > last_7d)
    last7d_otps = sum(1 for t in otps_received if datetime.fromisoformat(t) > last_7d)
    return {
        "total_numbers": len(numbers_taken), "total_otps": len(otps_received),
        "today_numbers": today_numbers, "today_otps": today_otps,
        "last24h_numbers": last24h_numbers, "last24h_otps": last24h_otps,
        "last7d_numbers": last7d_numbers, "last7d_otps": last7d_otps
    }

def log_global_activity(uid, action, details):
    if not os.path.exists(ACTIVITY_LOGS_FILE):
        with open(ACTIVITY_LOGS_FILE, "w") as f:
            json.dump([], f)
    try:
        with open(ACTIVITY_LOGS_FILE, "r") as f:
            logs = json.load(f)
    except Exception:
        logs = []
    now = datetime.now()
    logs.append({
        "uid": str(uid), "action": action, "details": details,
        "timestamp": now.isoformat(),
        "date": now.strftime("%d/%m/%Y"),
        "time": now.strftime("%H:%M:%S")
    })
    save_data(logs, ACTIVITY_LOGS_FILE)

def get_global_system_stats():
    stats = load_stats()
    now = datetime.now()
    today_midnight = datetime(now.year, now.month, now.day)
    last_7d = now - timedelta(days=7)
    total_n = total_o = today_n = today_o = seven_n = seven_o = 0
    for uid in stats:
        u = stats[uid]
        n_list = u.get("numbers_taken", [])
        o_list = u.get("otps_received", [])
        total_n += len(n_list)
        total_o += len(o_list)
        for t in n_list:
            dt = datetime.fromisoformat(t)
            if dt >= today_midnight:
                today_n += 1
            if dt >= last_7d:
                seven_n += 1
        for t in o_list:
            dt = datetime.fromisoformat(t)
            if dt >= today_midnight:
                today_o += 1
            if dt >= last_7d:
                seven_o += 1
    return today_n, today_o, seven_n, seven_o, total_n, total_o

# ==================== CHANNEL MEMBERSHIP CHECK ====================

async def check_channel_membership(bot, user_id, channel_id):
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return member.status in ["member", "administrator", "creator", "owner"]
    except Exception as e:
        print(f"Membership verification failed: {e}")
        # Return true on bot access issues to ensure continuous service
        return True

# ==================== SUPPORT TERMINAL (ALWAYS REACHABLE) ====================

def _panel_stats(p):
    """Live numbers for one panel: services, ranges, countries, 🔥 hot ranges, OTP traffic in the last hour."""
    pid = p["id"]
    svcs = [x for x in get_cached_services() if pid in (x.get("panels") or [])]
    rids = {r for r, o in _RANGE_PANEL.items() if o == pid}
    countries = {(_RANGE_META.get(r, {}).get("country") or "?") for r in rids}
    hot = sum(1 for r in rids if _RANGE_META.get(r, {}).get("hot"))
    t1h = sum(_range_hits(m) for m in _AUTO["pairs"].values() if m.get("panel") == pid)
    return svcs, rids, countries, hot, t1h

def panels_overview():
    lines = [head("NUMBER PANELS", "🛰️").rstrip("\n")]
    rows = []
    for p in PANELS:
        st = p["status"]
        if not p.get("enabled"):
            dot, info = "⚪", "not configured"
        elif st["online"] is None:
            dot, info = "🟡", "waiting for first sync…"
        elif st["online"]:
            dot, info = "🟢", "ONLINE" + (f" · synced {st['last_ok'].strftime('%H:%M:%S')}" if st.get("last_ok") else "")
        else:
            dot, info = "🔴", f"OFFLINE · <code>{html.escape(st.get('error') or 'error')}</code>"
        svcs, rids, countries, hot, t1h = _panel_stats(p)
        lines.append(
            f"\n{dot} <b>{html.escape(p['name'])}</b>  ▸ {info}\n"
            f"   🎛️ Services: <code>{st.get('services', 0)}</code> live  ·  🛰️ Ranges: <code>{len(rids)}</code>\n"
            f"   🌍 Countries: <code>{len([c for c in countries if c not in ('?', 'Unknown')])}</code>  ·  🔥 Hot: <code>{hot}</code>  ·  🔑 OTP/1h: <code>{t1h}</code>"
        )
        rows.append([InlineKeyboardButton(f"{dot} {p['name']} — details", callback_data=f"pnl_v_{p['id']}")])
    total, per_c, _ = network_traffic(1)
    lines.append(f"\n{DIV}\n🔗 <b>Merged in GET NUMBER:</b> <code>{len(get_cached_services())}</code> services · <code>{len(_RANGE_META)}</code> ranges\n"
                 f"🔑 <b>Network OTP (1h, all panels):</b> <code>{total}</code>\n"
                 f"<i>➕ /addpanel NAME URL KEY  ·  ➖ /delpanel NAME</i>")
    rows.append([InlineKeyboardButton("🔄 Refresh", callback_data="pnl_refresh"), InlineKeyboardButton("❌ Close", callback_data="pnl_close")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)

def panel_detail(pid):
    p = next((x for x in PANELS if x["id"] == pid), None)
    if not p:
        return "Panel not found.", InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Back", callback_data="pnl_refresh")]])
    svcs, rids, countries, hot, t1h = _panel_stats(p)
    url = str(p.get("base_url", ""))
    lines = [head(f"{p['name']} PANEL", "🛰️").rstrip("\n"),
             f"🌐 <code>{html.escape(url[:60])}</code>",
             f"🔐 Key: <code>{html.escape(str(p.get('api_key', ''))[:4])}••••</code>  ·  type: <code>{html.escape(p.get('kind') or 'yesms')}</code>",
             f"🛰️ Ranges: <code>{len(rids)}</code>  ·  🔥 Hot: <code>{hot}</code>  ·  🔑 OTP/1h: <code>{t1h}</code>", DIV]
    for x in svcs[:15]:
        mine = [r for r in x["ranges"] if _RANGE_PANEL.get(r) == pid]
        tag = f"{len(mine)} range(s)"
        if not mine:
            mine, tag = list(x["ranges"]), f"{len(x['ranges'])} shared range(s)"
        tops = ", ".join(("🔥" if _RANGE_META.get(r, {}).get("hot") else "") + f"<code>{r}</code>" for r in mine[:3])
        lines.append(f"{service_icon(x['sid'])} <b>{html.escape(str(x['sid']).upper())}</b> · {tag}\n   {tops}")
    if len(svcs) > 15:
        lines.append(f"<i>…and {len(svcs) - 15} more service(s)</i>")
    if not svcs:
        lines.append("<i>No services from this panel right now.</i>")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data=f"pnl_v_{pid}"), InlineKeyboardButton("↩️ Back", callback_data="pnl_refresh")]])
    return "\n".join(lines)[:4000], kb

async def panels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: health of every number panel (also the 🛰️ NUMBER PANELS button in ADMIN PANEL → SYSTEM CORE)."""
    if not is_admin(update.effective_user.id):
        return
    text, kb = panels_overview()
    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=kb)

async def setrangegroup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """v16.12 Admin: send /setrangegroup INSIDE the range group → saves this group as the
    RANGE announcement group (OTP cards keep going to the OTP group)."""
    if not is_admin(update.effective_user.id):
        return
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup"):
        await update.message.reply_text("⚠️ Send <code>/setrangegroup</code> INSIDE the range group.", parse_mode="HTML")
        return
    cfg = load_sys_config() or {}
    cfg["range_group_id"] = chat.id
    cfg["range_group_title"] = chat.title or ""
    save_sys_config(cfg)
    await update.message.reply_text(
        f"✅ <b>Range group set</b>\n🆔 <code>{chat.id}</code>\n📛 {html.escape(chat.title or '')}\n\n"
        f"🆕 New <b>range</b> announcements will be posted here.\n🔴 <b>OTP</b> cards stay in the OTP group.",
        parse_mode="HTML")

async def group_id_logger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """v16.15: log group chat ids when an admin writes in a group (helps discover/set the range group)."""
    try:
        chat = update.effective_chat
        user = update.effective_user
        if chat and chat.type in ("group", "supergroup") and user and is_admin(user.id):
            print(f"[group] id={chat.id} title={chat.title!r} — /setrangegroup here sets the RANGE group")
    except Exception:
        pass

async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        f"✨ — 👨‍💻 <b>SUPPORT</b> — ✨\n"
        f"<b>{html.escape(SUPPORT_NAME)}</b> 👑 <b>ADMIN</b>\n\n"
        f"🆔 <code>{SUPPORT_HANDLE}</code>"
    )
    keyboard = InlineKeyboardMarkup([
        [make_inline_keyboard_button("💬 Contact Support", url=SUPPORT_LINK)],
        [close_button()],
    ])
    await update.effective_message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")

async def traffic_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _tm = await update.effective_message.reply_text(traffic_card(), parse_mode="HTML", reply_markup=traffic_actions_kb())

async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not get_cached_services():
        await _do_liveaccess_fetch()
    await update.effective_message.reply_text(stock_card(), parse_mode="HTML", reply_markup=stock_actions_kb())

# ==================== LEADERBOARD SECTION ====================

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lang = get_user_lang(uid)
    if is_user_banned(uid):
        await update.message.reply_text(LANG_TEXTS[lang]["banned"], parse_mode="HTML", reply_markup=main_keyboard(uid))
        return
    stats_data = load_stats()
    today_midnight = get_date_reset_time()
    user_data_all = load_data(USER_DATA_FILE)
    user_today_counts = []
    for uid_str, user_stats in stats_data.items():
        otps_received = user_stats.get("otps_received", [])
        today_count = sum(1 for ts in otps_received if datetime.fromisoformat(ts) >= today_midnight)
        if today_count > 0:
            name = user_data_all.get(uid_str, {}).get("full_name") or user_data_all.get(uid_str, {}).get("username") or f"User {uid_str}"
            user_today_counts.append((uid_str, today_count, html.escape(name)))
    user_today_counts.sort(key=lambda x: x[1], reverse=True)
    top10 = user_today_counts[:10]

    if not top10:
        empty = "🚫 আজ এখনও কেউ ওটিপি হান্ট করেনি।\n<i>প্রথম হান্টার হিসেবে সেরা হওয়ার সুযোগ আপনার!</i>" if lang == "bn" else "🚫 No OTP hunts recorded today.\n<i>Be the first hunter on the board!</i>"
        msg = head("TOP HUNTERS", "🏆") + empty
    else:
        msg = head("TOP HUNTERS", "🏆")
        msg += "🎖️ <b>আজকের সেরা ১০ হান্টার</b>\n\n" if lang == "bn" else "🎖️ <b>Todays finest 10 hunters</b>\n\n"
        for idx, (uid_str, count, name) in enumerate(top10, 1):
            medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else num_badge(idx)
            msg += f"{medal} <b>{name}</b>  ▸  🔑 <code>{count}</code>\n"
        msg += f"\n{DIV}\n" + ("📊 <i>প্রতি রাত ১২টায় অটো রিসেট হয়</i>" if lang == "bn" else "📊 <i>Auto resets every midnight</i>")

    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=main_keyboard(uid))

# ==================== 2FA CODE GENERATOR SECTION ====================

def generate_2fa_code(secret_key):
    try:
        clean_secret = secret_key.replace(" ", "").strip()
        totp = pyotp.TOTP(clean_secret)
        return totp.now(), clean_secret
    except Exception:
        return None, None

async def get_2fa_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lang = get_user_lang(uid)
    if is_user_banned(uid):
        await update.message.reply_text(LANG_TEXTS[lang]["banned"], parse_mode="HTML", reply_markup=main_keyboard(uid))
        return
    context.user_data["mode"] = "get_2fa"
    await update.message.reply_text(
        LANG_TEXTS[lang]["get_2fa_prompt"],
        parse_mode="HTML",
        reply_markup=cancel_keyboard(uid)
    )

async def process_2fa_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lang = get_user_lang(uid)
    secret_key = update.message.text.strip()
    context.user_data["mode"] = None
    otp_code, clean_key = generate_2fa_code(secret_key)
    if otp_code is None:
        await update.message.reply_text(
            LANG_TEXTS[lang]["2fa_invalid"],
            parse_mode="HTML",
            reply_markup=main_keyboard(uid)
        )
        return
    now = datetime.now()
    final_msg = (
        f"{head('2FA DECRYPTED', '🔐')}"
        f"<blockquote>🔑 <b>Secret</b>  ▸ <code>{html.escape(clean_key)}</code></blockquote>\n"
        f"<blockquote>🔢 <b>PIN</b>     ▸ <code>{otp_code}</code></blockquote>\n"
        f"<blockquote>⏳ <b>Valid</b>   ▸ <code>30 SECONDS</code></blockquote>\n\n"
        f"📅 {now.strftime('%d %B, %Y')}  •  🕐 {now.strftime('%I:%M %p')}\n\n"
        f"{BRAND_SHORT}"
    )
    await update.message.reply_text(final_msg, parse_mode="HTML", reply_markup=main_keyboard(uid))

# ==================== GET NUMBER INTERFACE (LIVEACCESS) ====================

def _build_services_keyboard(services, user_id, multi=False):
    """ms_fast-style service picker: full-width blue buttons (plain UPPER labels), red Close at bottom."""
    btns = []
    for i, svc in enumerate(services):
        sid = str(svc.get("sid", f"Service {i+1}"))
        btns.append(make_inline_keyboard_button(sid.upper(), callback_data=f"svc_{i}", style="primary"))
    rows = [[b] for b in btns]
    # v16.11: CUSTOM RANGE is available in the normal GET NUMBER flow too (not only MULTI)
    rows.append([make_inline_keyboard_button(BTN["en"]["custom_range"], callback_data="custom_range", style="success")])
    rows.append([make_inline_keyboard_button("Close", callback_data="ui_close", style="danger")])
    return InlineKeyboardMarkup(rows)

def _group_ranges_by_country(ranges, max_countries=4):
    """[(flag, country, [range idx...])] — one button per country, ranges kept underneath.
    v16.2 AUTO-TRAFFIC SYSTEM: only countries with LIVE traffic stay (hot or hits>0),
    sorted hot → traffic, capped at max_countries. Falls back to the first groups when
    no traffic data exists yet, so the picker never breaks."""
    groups, order = {}, []
    for i, r in enumerate(ranges):
        mm = _RANGE_META.get(_norm_rid(r)) or {}
        flag, cname = mm.get("flag"), mm.get("country")
        if not cname:
            flag, cname = get_country_info(_rid_prefix(r))
        flag = flag or "🌍"
        k = f"{flag} {cname}"
        if k not in groups:
            groups[k] = {"label": k, "flag": flag, "country": cname, "idxs": [], "hot": False, "hits": 0}
            order.append(k)
        g = groups[k]
        g["idxs"].append(i)
        g["hot"] = g["hot"] or bool(mm.get("hot"))
        g["hits"] += int(mm.get("hits") or 0)
    out = [groups[k] for k in order]
    out.sort(key=lambda g: (0 if g["hot"] else 1, -g["hits"]))
    live = [g for g in out if g["hot"] or g["hits"] > 0]     # auto-traffic only
    if live:
        out = live
    return out[:max_countries]

def _build_countries_keyboard(countries, service_idx, user_id):
    """ms_fast-style country picker: full-width green [flag country] buttons, red Back at bottom."""
    btns = []
    for ci, g in enumerate(countries[:40]):
        btns.append(make_inline_keyboard_button(f"{g['flag']} {g['country']}", callback_data=f"cty_{ci}", style="success"))
    rows = [[b] for b in btns]
    rows.append([make_inline_keyboard_button("Back", callback_data="back_services", style="danger")])
    return InlineKeyboardMarkup(rows)

def _build_ranges_keyboard(group, ranges, service_idx, ci, user_id):
    """Range picker for one country: 🤖 AUTO BEST on top, then every auto-added range with live traffic."""
    lang = get_user_lang(user_id)
    s_ok, s_pri, s_dgr = _styles(lang)
    rows = [[make_inline_keyboard_button("🤖 AUTO BEST RANGE", callback_data=f"rauto_{ci}", style=s_ok)]]
    btns = []
    for i in group["idxs"][:30]:
        if i >= len(ranges):
            continue
        r = str(ranges[i])
        mm = _RANGE_META.get(_norm_rid(r)) or {}
        hits = int(mm.get("hits") or 0)
        sr = range_success_rate(r)
        label = ("🔥 " if mm.get("hot") else "📡 ") + r + (f" · {hits}/h" if hits else "") + (f" · ✅{sr}%" if sr >= 0 else "")
        if len(active_panels()) > 1:
            label += f" · {panel_for_range(r)['name'][:5]}"
        btns.append(make_inline_keyboard_button(label, callback_data=f"rng_{i}", style=s_pri))
    rows += [btns[j:j+2] for j in range(0, len(btns), 2)]
    rows.append([make_inline_keyboard_button(BTN[lang]["back"], callback_data=f"svc_{service_idx}"), close_button()])
    return InlineKeyboardMarkup(rows)

def _build_qty_keyboard(user_id):
    lang = get_user_lang(user_id)
    s_ok, s_pri, s_dgr = _styles(lang)
    counts = [(2, "⚡", s_pri), (3, "🔥", s_pri), (5, "💎", s_ok), (10, "👑", s_ok)]
    btns = [make_inline_keyboard_button(f"{e} {c} {sc('NODES')}", callback_data=f"mqty_{c}", style=st) for c, e, st in counts]
    rows = [btns[:2], btns[2:]]
    rows.append([make_inline_keyboard_button(BTN[lang]["back"], callback_data="back_services", style=s_dgr)])
    return InlineKeyboardMarkup(rows)

async def show_app_selection(update, context, multi=False):
    uid = update.effective_user.id
    lang = get_user_lang(uid)
    if is_user_banned(uid):
        await update.message.reply_text(LANG_TEXTS[lang]["banned"], parse_mode="HTML", reply_markup=main_keyboard(uid))
        return
    context.user_data["multi_mode"] = bool(multi)
    services = filter_visible_services(get_cached_services())
    if not services:
        await _do_liveaccess_fetch()
        services = filter_visible_services(get_cached_services())
    if not services:
        err_msg = (
            f"{head('MAINTENANCE', '🚧')}⏳ All services are temporarily offline.\n<i>Please try again shortly.</i>"
            if lang == "en" else f"{head('MAINTENANCE', '🚧')}⏳ সব সার্ভিস সাময়িকভাবে অফলাইনে আছে।\n<i>কিছুক্ষণ পর আবার চেষ্টা করুন।</i>"
        )
        if is_admin(uid):
            diag = ["", "🩺 <b>Panel status (admin):</b>"]
            for _p in active_panels():
                _st = _p.get("status") or {}
                _state = "🟢 online" if _st.get("online") else "🔴 offline"
                _err = _st.get("error") or ("no data yet" if not _st.get("online") else "")
                diag.append(f"▸ <b>{html.escape(_p['name'])}</b>: {_state}"
                            + (f" — <code>{html.escape(str(_err)[:80])}</code>" if _err else ""))
            err_msg += "\n".join(diag)
        await update.message.reply_text(err_msg, parse_mode="HTML", reply_markup=main_keyboard(uid))
        return
    context.user_data["la_services"] = services
    keyboard = _build_services_keyboard(services, uid, multi=multi)
    await update.message.reply_text(
        node_market_header(lang, multi=multi),
        parse_mode="HTML",
        reply_markup=keyboard
    )

# ==================== AUTO MONITOR LOOP (DECRYPTER) ====================

OWNER_LOOKBACK_SEC = 24 * 3600     # a number stays "owned" by the user who took it for 24h
_MONITOR_STARTED = {"ts": None}

def number_owner(num, range_db):
    """Who gets this OTP?  live allocation first, then the persisted owner map (datarange.json)."""
    if num in _RELEASED or (range_db.get(num) or {}).get("released"):
        return None
    d = active_numbers.get(num)
    if d:
        return d
    info = range_db.get(num)
    if info and info.get("user_id"):
        ts = float(info.get("ts") or 0)
        if not ts or datetime.now().timestamp() - ts <= OWNER_LOOKBACK_SEC:
            return {"uid": int(info["user_id"]), "range": info.get("range", ""), "panel": "yesms"}
    return None

def otp_group_card(service_name, otp_code, full_sms):
    """v16.14 star-card OTP feed — SERVICE + OTP only (the range goes to the range group)."""
    star = "⭐️" * 15
    return (
        f"{star}\n"
        f"⭐️ <b>{BRAND_NAME}</b> ⭐️\n"
        f"⭐️✨ <b>{BRAND_NAME} OTP Received</b> ✨⭐️\n"
        f"⚙️ <b>Service</b> : {service_icon(service_name)} {html.escape(str(service_name))}\n"
        f"🔑 <b>OTP Code</b> : <code>{html.escape(str(otp_code))}</code>\n\n"
        f"<blockquote>{html.escape(str(full_sms))}</blockquote>\n"
        f"⭐️──────────────────⭐️"
    )

def otp_group_buttons():
    """v16.10 OTP-group buttons (reference screenshot): blue [Get Number ↗] [Join Channel ↗]."""
    chan = (load_sys_config() or {}).get("official_channel_link") or "https://t.me/+wP66wN0oXT8xMzY9"
    return InlineKeyboardMarkup([
        [
            make_inline_keyboard_button("📱 Get Number ↗", url="https://t.me/numbergenfast_bot", style="primary"),
            make_inline_keyboard_button("📣 Join Channel ↗", url=chan, style="primary")
        ]
    ])

async def monitor_loop(app):
    _MONITOR_STARTED["ts"] = datetime.now().timestamp()
    while True:
        try:
            _LOOP_TICK["monitor"] = time.time()
            otps = await panel_success_otps()
            if otps:
                paid_data = load_data(PAID_SMS_FILE)
                range_db = load_data(DATA_RANGE_FILE)
                paid_keys_set = set(paid_data.keys())
                processed_in_session = set()

                for otp in otps:
                    num = normalize_number(otp.get("number", ""))
                    full_sms = otp.get('message') or otp.get('otp') or otp.get('sms') or "No SMS Content"
                    otp_code = extract_otp(full_sms)
                    otp_id = str(otp.get("otp_id", ""))
                    sms_key = otp_id if otp_id else f"{num}_{full_sms}"

                    if sms_key in paid_keys_set or sms_key in processed_in_session:
                        continue
                    # history guard: codes older than 15 min before this boot are archived, never re-paid
                    ots = otp.get("ts")
                    if ots and ots < _MONITOR_STARTED["ts"] - 900:
                        paid_keys_set.add(sms_key)
                        paid_data[sms_key] = {"uid": 0, "otp": otp_code, "archived": True}
                        continue
                    details = number_owner(num, range_db)
                    if details:
                        paid_keys_set.add(sms_key)
                        processed_in_session.add(sms_key)
                        paid_data[sms_key] = {"uid": details["uid"], "otp": otp_code}

                        # v16.15: reward uses the per-panel rate (admin-tunable in PANEL RATES)
                        _rate = panel_rate(otp.get("panel"))
                        await update_db_balance(details["uid"], _rate)
                        add_otp_received(details["uid"])
                        log_global_activity(details["uid"], "OTP_RECEIVED", {"number": num, "otp": otp_code, "sms": full_sms})
                        _OTP_DONE[num] = otp_code
                        range_stat_bump(details.get("range") or active_numbers.get(num, {}).get("range") or range_db.get(num, {}).get("range", ""), "otp")

                        num_range_info = range_db.get(num, {}).get("range", "")
                        if not num_range_info:
                            num_range_info = active_numbers.get(num, {}).get("range", "")
                        if not num_range_info and num:
                            _d = re.sub(r'\D', '', str(num))
                            num_range_info = (_d[:-3] + 'XXX') if len(_d) > 3 else (_d + 'XXX')

                        country_flag, country_name = get_country_info(num)
                        service_name = detect_service(full_sms)
                        service_logo = get_service_logo(service_name)
                        clean_num = num.replace('+', '').strip()
                        full_number = f"+{clean_num}"
                        masked_number = f"+{mask_number(clean_num)}"

                        safe_full_sms = html.escape(str(full_sms))
                        safe_otp_code = html.escape(str(otp_code))

                        user_msg = (
                            f"{head('OTP RECEIVED', '🎉')}"
                            f"🛰️ <b>Range</b>   ▸ <code>{num_range_info}</code>\n"
                            f"🌍 <b>Origin</b>  ▸ {country_flag} {country_name}\n"
                            f"📱 <b>Service</b> ▸ {service_logo}\n"
                            f"🔥 📟 <b>Number</b>  ▸ <code>{full_number}</code>\n"
                            f"🔑 <b>OTP Key</b> ▸ <code>{safe_otp_code}</code>\n\n"
                            f"📩 <b>FULL PACKET</b>\n"
                            f"<blockquote expandable><code>{safe_full_sms}</code></blockquote>\n"
                            f"🪙 <b>+{_rate:.2f} BDT</b> rewarded to your wallet 💰"
                        )

                        # v16.14 star-card OTP feed — service + OTP only (range → range group)
                        group_msg = otp_group_card(service_name, otp_code, full_sms)
                        group_buttons = otp_group_buttons()

                        try:
                            _cp = copy_button("🔑 COPY OTP", otp_code)
                            _kb = InlineKeyboardMarkup([[_cp]]) if _cp else None
                            _otp_msg = await send_fx(app.bot, details["uid"], user_msg, effect="fire", reply_markup=_kb)
                        except Exception as e:
                            print(f"❌ User Message Send Fail: {e}")

                        if otp_code:
                            _OWN_GROUP_CODES[str(otp_code)] = time.time()
                        try:
                            await app.bot.send_message(OTP_GROUP_ID, group_msg, parse_mode="HTML", reply_markup=group_buttons)
                        except Exception as e:
                            print(f"❌ Group Send Fail: {e}")

                        save_data(paid_data, PAID_SMS_FILE)

                # persist archived (pre-boot) keys too, so they are never re-evaluated
                if len(paid_data) != len(paid_keys_set) or any(v.get("archived") for v in paid_data.values() if isinstance(v, dict)):
                    save_data(paid_data, PAID_SMS_FILE)

                current_time = datetime.now()
                for num_key in list(active_numbers.keys()):
                    entry = active_numbers[num_key]
                    if 'timestamp' not in entry:
                        entry['timestamp'] = current_time
                    elif (current_time - entry['timestamp']).total_seconds() > 3600:
                        del active_numbers[num_key]

        except Exception as e:
            print(f"Monitor Error: {e}")
        await asyncio.sleep(CHECK_INTERVAL)

# ==================== FETCH NODES WORKERS ====================

async def _2oo9_getnum(p, range_str):
    rid = _rid_prefix(range_str) or re.sub(r"\D", "", str(range_str))
    try:
        r = await _fetch_retry(lambda: p["client"].post(f"{p['base_url']}/getnum", json={"rid": rid}), attempts=2, backoff=1.0)
        d = r.json()
        meta = (d.get("meta") or {}) if isinstance(d, dict) else {}
        if meta.get("code") != 200:
            msg = "Out of stock for this range!" if meta.get("code") == 2946 else str((d or {}).get("message") or f"HTTP {r.status_code}")
            _LAST_ALLOC_ERROR["msg"] = msg[:120]
            print(f"[{p['id']}] getnum {rid}: {msg}")
            return None
        _LAST_ALLOC_ERROR["msg"] = ""
        x = d.get("data") or {}
        number = x.get("full_number") or x.get("no_plus_number") or x.get("number")
        if number:
            return {"number": str(number), "otp_now": False, "otp": None, "sms": None,
                    "country": x.get("country"), "operator": x.get("operator"), "panel": p["id"]}
    except Exception as e:
        print(f"[{p['id']}] getnum error: {e}")
    return None

async def _panel_getnum(p, range_str):
    if _is_2oo9(p):
        return await _2oo9_getnum(p, range_str)
    try:
        r = await _fetch_retry(lambda: p["client"].post(
            f"{p['base_url']}/api/allocate_number",
            json={"range_id": str(range_str).strip().upper()}
        ), attempts=2, backoff=1.0)
        data = r.json()
        if isinstance(data, dict) and data.get("error"):
            _LAST_ALLOC_ERROR["msg"] = str(data.get("error"))[:120]
            print(f"[{p['id']}] allocate_number: {data.get('error')}")
            return None
        _LAST_ALLOC_ERROR["msg"] = ""
        d = data.get("data", {}) if isinstance(data, dict) else {}
        number = d.get("full_number") or d.get("no_plus_number") or d.get("number")
        if number:
            return {
                "number": str(number),
                "otp_now": bool(d.get("otp_now", False)),
                "otp": d.get("otp"),
                "sms": d.get("sms"),
                "country": d.get("country"),
                "operator": d.get("operator"),
                "panel": p["id"],
            }
    except Exception as e:
        print(f"[{p['id']}] allocate_number error: {e}")
    return None

async def fetch_number_async(range_str, panel=None):
    """Allocate from the panel that owns the range; if it fails, try the other enabled panel(s)."""
    first = panel_by_id(panel) if panel else panel_for_range(range_str)
    tried = []
    for p in [first] + [x for x in active_panels() if x["id"] != first["id"]]:
        if not p.get("enabled") and p is not first:
            continue
        tried.append(p["id"])
        res = await _panel_getnum(p, range_str)
        if res:
            return res
    return None

def _is_node_busy(clean_num, uid):
    """Anti-hijack guard: True if node is already owned by another user."""
    owner = active_numbers.get(clean_num)
    return bool(owner) and owner.get("uid") != uid

def sibling_ranges(range_text, sid):
    """Other auto-added ranges of the same service + country — used for AUTO FALLBACK when a range is out of stock."""
    key, rid = _svc_key(sid), _norm_rid(range_text)
    country = range_country(rid)[1]
    for s in get_cached_services():
        if _svc_key(s["sid"]) == key:
            return [r for r in s["ranges"] if _norm_rid(r) != rid and range_country(r)[1] == country]
    return []

async def _alloc_chain(range_text, sid, uid):
    """AUTO FALLBACK chain: requested range → sibling ranges (same service + country) → other panels."""
    for cand in [range_text] + sibling_ranges(range_text, sid)[:4]:
        res = None
        for _attempt in range(3):
            res = await fetch_number_async(cand)
            if not res or not res.get("number"):
                break
            if _is_node_busy(normalize_number(res["number"]), uid):
                res = None
                continue
            break
        if res and res.get("number"):
            if cand != range_text:
                print(f"[auto] fallback {range_text} → {cand} ({_LAST_ALLOC_ERROR.get('msg') or 'no stock'})")
            res["_cand"] = cand
            return res
    return None

def _fmt_id(num):
    """Gamer-ID style formatting: 2246599924004 → +2246 5999 2400 4"""
    n = str(num).lstrip("+")
    return "+" + " ".join(n[i:i + 4] for i in range(0, len(n), 4))


def _id_mask(num, stage):
    """Progressive gamer-ID reveal: 0 = mostly masked, 1 = half revealed, 2 = full ID."""
    n = str(num).lstrip("+")
    if stage >= 2 or len(n) <= 6:
        return _fmt_id(n)
    groups = [n[i:i + 4] for i in range(0, len(n), 4)]
    if stage == 0:
        shown = [groups[0]] + ["▓" * 4] * (len(groups) - 2) + [n[-2:]]
    else:
        half = max(1, len(groups) // 2)
        shown = [g if i < half else "▓" * 4 for i, g in enumerate(groups)]
        shown[-1] = n[-2:]
    return "+" + " ".join(shown)


async def premium_number_entry(bot, msg, uid, sid, clean_num):
    """SIMPLE PREMIUM ENTRY (v16.9) — ONE simple animation: straight to the NEW NUMBER
    screen → one BIG 😎 pop → 🎉 confetti banner. Instant, no multi-frame sequences.
    Respects /anim off (straight to the final screen, no pop, no banner)."""
    try:
        await msg.edit_text("NEW NUMBER", reply_markup=new_number_kb(uid, sid if sid else "SMS"))
    except Exception as e:
        print(f"fast_allocate edit error: {e}")
        return
    if not anim_enabled():
        return
    # the ONE simple premium animation: a single big pop + confetti banner
    await react(bot, msg.chat_id, msg.message_id, "😎", big=True)
    try:
        await send_fx(bot, uid, f"👑 <b>{_fmt_id(clean_num)}</b> 🔥 · <b>GAMER ID READY</b> 😎⚡", effect="party")
    except Exception:
        pass


async def fast_allocate_number(query, context, range_text, sid):
    uid = query.from_user.id
    lang = get_user_lang(uid)
    if is_user_banned(uid):
        await query.message.edit_text(LANG_TEXTS[lang]["banned"], parse_mode="HTML")
        return
    res = None
    requested_range = range_text
    radar = AllocRadar(query.message, range_text).start()
    try:
        res = await _alloc_chain(range_text, sid, uid)
    except Exception as e:
        await radar.stop()
        await query.message.edit_text(f"❌ Server error: {str(e)[:100]}")
        return
    await radar.stop()
    if res and res.get("_cand"):
        range_text = res["_cand"]
    if not res or not res.get("number"):
        await query.message.edit_text(
            node_alloc_fail_card(lang),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                make_inline_keyboard_button(BTN[lang]["back"], callback_data="back_services", style="danger" if lang == "en" else None)
            ]])
        )
        return

    clean_num = normalize_number(res["number"])
    add_number_taken(uid, 1)
    last_range[uid] = range_text
    active_numbers[clean_num] = {"uid": uid, "range": range_text, "timestamp": datetime.now(), "panel": res.get("panel")}
    save_number_range_info(uid, clean_num, range_text)

    instant = bool(res.get("otp_now") and res.get("otp"))
    if instant:
        add_otp_received(uid)          # stats only — the monitor delivers & pays the OTP card

    # ms_fast-style result screen with premium entry animation: "NEW NUMBER" + green service / blue copy / Change Number / OTP Group
    quick_pick_set(uid, sid, range_text)
    await premium_number_entry(query.get_bot(), query.message, uid, sid if sid else "SMS", clean_num)

async def worker():
    while True:
        task = await request_queue.get()
        try:
            if task['type'] == 'process_numbers':
                await process_numbers(task['update'], task['context'], task['range_text'], task['count'])
            elif task['type'] == 'search_otp':
                await perform_otp_search(task['update'], task['context'], task['target_num'])
            elif task['type'] == 'auto_number':
                await process_auto_number(task['update'], task['context'], task['range_text'])
        except Exception as e:
            print(f"Worker Error: {e}")
        finally:
            request_queue.task_done()

# ==================== DEEP LINK AUTO ALLOCATION ====================

async def process_auto_number(update, context, range_text):
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    lang = get_user_lang(uid)
    if is_user_banned(uid):
        await context.bot.send_message(chat_id=chat_id, text=LANG_TEXTS[lang]["banned"], parse_mode="HTML", reply_markup=main_keyboard(uid))
        return
    status_msg = await context.bot.send_message(chat_id=chat_id, text="⚡ <b>INJECTING NODE...</b>", parse_mode="HTML")
    try:
        res = None
        for _attempt in range(3):
            res = await fetch_number_async(range_text)
            if not res or not res.get("number"):
                break
            if _is_node_busy(normalize_number(res["number"]), uid):
                res = None
                continue
            break
        if not res or not res.get("number"):
            await status_msg.edit_text(node_alloc_fail_card(lang), parse_mode="HTML")
            return
        generated_num = normalize_number(res["number"])
        add_number_taken(uid, 1)
        last_range[uid] = range_text
        active_numbers[generated_num] = {"uid": uid, "range": range_text, "timestamp": datetime.now(), "panel": res.get("panel")}
        save_number_range_info(uid, generated_num, range_text)

        country_flag, country_name = get_country_info(generated_num)
        service_logo = get_service_logo("SMS SERVICE")

        instant = bool(res.get("otp_now") and res.get("otp"))
        if instant:
            add_otp_received(uid)
        # v16.4: premium gamer-ID entry on every allocation path
        await premium_number_entry(context.bot, status_msg, uid, "SMS", generated_num)
    except Exception as e:
        print(f"Auto Number Error: {e}")
        await status_msg.edit_text(f"❌ Error: {str(e)}")

# ==================== MANUALLY TRIGGERED PROCESS (SINGLE + MULTI BURST) ====================

async def process_numbers(update_or_query, context, range_text, count):
    if isinstance(update_or_query, Update) and update_or_query.callback_query:
        uid = update_or_query.callback_query.from_user.id
        chat_id = update_or_query.callback_query.message.chat_id
    else:
        uid = update_or_query.effective_user.id
        chat_id = update_or_query.effective_chat.id
    lang = get_user_lang(uid)
    if is_user_banned(uid):
        await context.bot.send_message(chat_id=chat_id, text=LANG_TEXTS[lang]["banned"], parse_mode="HTML", reply_markup=main_keyboard(uid))
        return

    # admin per-pick value is honoured as-is (unlimited); /multi bursts stay capped at MULTI_MAX
    count = max(1, min(int(count), max(MULTI_MAX, get_numbers_per_pick())))
    sid = context.user_data.get("la_sid", "") if getattr(context, "user_data", None) else ""
    if count > 1:
        burst_lbl = f"🎰 <b>BURST INJECTING</b> ⚡\n<code>{count}</code> nodes from <code>{html.escape(str(range_text))}</code>..."
    else:
        burst_lbl = f"🔍 <b>ALLOCATING NODE</b> ⚡\nRange <code>{html.escape(str(range_text))}</code>..."
    status_msg = await context.bot.send_message(chat_id=chat_id, text=burst_lbl, parse_mode="HTML")
    try:
        add_number_taken(uid, count)
        last_range[uid] = range_text
        requested_range = range_text
        # AUTO FALLBACK chain: the picked range first, then sibling ranges of the same service+country
        chain = [range_text] + (sibling_ranges(range_text, sid)[:6] if sid else [])
        seen_batch, valid_results, used_ranges = set(), [], []
        for cand in chain:
            empty_waves = 0
            while len(valid_results) < count and empty_waves < 2:
                need = count - len(valid_results)
                wave = min(need, 10)                       # 10 parallel allocations per wave
                results = await asyncio.gather(*(fetch_number_async(cand) for _ in range(wave)))
                got = 0
                for r in results:
                    if not r or not r.get("number"):
                        continue
                    cn = normalize_number(r["number"])
                    if not cn or cn in seen_batch or _is_node_busy(cn, uid):
                        continue
                    seen_batch.add(cn)
                    r["range"] = cand
                    valid_results.append(r)
                    got += 1
                if got == 0:
                    empty_waves += 1
                elif cand not in used_ranges:
                    used_ranges.append(cand)
                if got < wave:                             # this range is running dry
                    break
            if len(valid_results) >= count:
                break
        if valid_results and valid_results[0].get("range"):
            range_text = valid_results[0]["range"]
            if sid:
                quick_pick_set(uid, sid, range_text)
            if range_text != requested_range:
                print(f"[auto] burst fallback {requested_range} → {range_text}")

        if not valid_results:
            await status_msg.edit_text(node_alloc_fail_card(lang), parse_mode="HTML")
            return

        num_entries = []
        for r in valid_results:
            clean_num = normalize_number(r["number"])
            if clean_num:
                r_range = r.get("range") or range_text
                active_numbers[clean_num] = {"uid": uid, "range": r_range, "timestamp": datetime.now(), "panel": r.get("panel") if isinstance(r, dict) else None}
                save_number_range_info(uid, clean_num, r_range)
                num_entries.append({
                    "num": clean_num,
                    "otp_now": r.get("otp_now", False),
                    "otp": r.get("otp"),
                    "sms": r.get("sms"),
                })

        if not num_entries:
            await status_msg.edit_text(node_alloc_fail_card(lang), parse_mode="HTML")
            return

        country_flag, country_name = get_country_info(num_entries[0]["num"])
        if country_name == "Unknown":
            country_flag, country_name = range_country(range_text)
        service_logo = get_service_logo(sid or detect_service(range_text))
        num_lines = []
        for i, entry in enumerate(num_entries, 1):
            badge = num_badge(i)
            if entry["otp_now"] and entry["otp"]:
                otp_safe = html.escape(str(entry["otp"]))
                sms_safe = html.escape(str(entry.get("sms") or ""))
                add_otp_received(uid)
                line = (
                    f"{badge} 🔥 📟 <code>+{entry['num']}</code>\n"
                    f"    🔑 <code>{otp_safe}</code>"
                    + (f"\n    📩 <code>{sms_safe}</code>" if sms_safe else "")
                )
            else:
                line = f"{badge} 🔥 📟 <code>+{entry['num']}</code>"
            num_lines.append(line)

        # Telegram caps a message at 4096 chars → big bursts overflow into follow-up messages
        overflow = []
        if len(num_lines) > 30:
            overflow, num_lines = num_lines[30:], num_lines[:30]
        num_list_text = "\n".join(num_lines)
        any_instant = any(e["otp_now"] and e["otp"] for e in num_entries)
        if any_instant:
            sms_status = "🟢 <b>INSTANT OTP DECRYPTED ABOVE</b>"
        else:
            sms_status = "📡 <b>Auto-scan armed</b> — OTP আসতেই এখানে + গ্রুপে পৌঁছে যাবে ⚡"

        burst_title = "MULTI BURST" if count > 1 else "NODE ALLOCATED"
        burst_icon = "🎰" if count > 1 else "🛰️"
        final_text = (
            f"{head(burst_title, burst_icon)}"
            f"🌍 <b>Origin</b>  ▸ {country_flag} {country_name}\n"
            f"🛰️ <b>Range</b>   ▸ <code>{range_text}</code>\n"
            f"📱 <b>Service</b> ▸ {service_logo}\n"
            f"🔢 <b>Nodes</b>   ▸ <code>{len(num_entries)}/{count}</code>\n\n"
            f"📋 <b>ALLOCATED CHANNELS</b>\n"
            f"<blockquote>{num_list_text}</blockquote>\n"
            f"{sms_status}"
        )
        if len(used_ranges) > 1 or range_text != requested_range:
            final_text += (f"\n🤖 <i>Auto range chain: " + " → ".join(f"<code>{html.escape(str(r))}</code>" for r in used_ranges) + "</i>")
        # v16.4: every allocation result gets the premium gamer-ID entry → NEW NUMBER screen
        await premium_number_entry(context.bot, status_msg, uid, sid or "SMS", num_entries[0]["num"])
        # big bursts (>5 numbers): the detailed list follows as separate message(s)
        _first = num_entries[0] if len(num_entries) == 1 else None
        keyboard = number_actions_kb(lang, _first["num"] if _first else None,
                                     (_first.get("otp") if _first and _first.get("otp_now") else None))
        if len(num_entries) > 5:
            try:
                await context.bot.send_message(chat_id=chat_id, text=final_text, parse_mode="HTML")
            except Exception:
                pass
        for j in range(0, len(overflow), 30):
            chunk = "\n".join(overflow[j:j + 30])
            last = j + 30 >= len(overflow)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"📋 <b>ALLOCATED CHANNELS</b> <i>(cont. {31 + j}–{30 + j + len(overflow[j:j + 30])})</i>\n<blockquote>{chunk}</blockquote>",
                parse_mode="HTML", reply_markup=keyboard if last else None)
    except Exception as e:
        print(f"Process Number Error: {e}")
        await status_msg.edit_text(f"❌ Error: {str(e)}")

# ==================== PERFORM DECRYPTED OTP SEARCH ====================

async def perform_otp_search(update, context, target_num):
    uid = update.effective_user.id
    lang = get_user_lang(uid)
    if is_user_banned(uid):
        await update.message.reply_text(LANG_TEXTS[lang]["banned"], parse_mode="HTML", reply_markup=main_keyboard(uid))
        return
    status_msg = await update.message.reply_text(LANG_TEXTS[lang]["search_otp_searching"], parse_mode="HTML")
    try:
        all_otps = await panel_success_otps()
        if active_panels():
            found_otps = [o for o in all_otps if normalize_number(o.get("number", "")) == target_num]

            if not found_otps:
                await status_msg.delete()
                not_found = (
                    f"{head('NO OTP FOUND', '🚫')}"
                    f"📟 নম্বর ▸ <code>+{target_num}</code>\n"
                    f"⏳ স্ট্যাটাস ▸ কোনো সক্রিয় প্যাকেট পাওয়া যায়নি।"
                    if lang == "bn" else
                    f"{head('NO OTP FOUND', '🚫')}"
                    f"📟 Number ▸ <code>+{target_num}</code>\n"
                    f"⏳ Status ▸ No active packet recorded."
                )
                await update.message.reply_text(not_found, parse_mode="HTML", reply_markup=main_keyboard(uid))
            else:
                await status_msg.delete()
                paid_data = load_data(PAID_SMS_FILE)
                for o in found_otps:
                    full_sms = o.get('message') or o.get('otp') or o.get('sms') or "No Content"
                    otp_code = extract_otp(full_sms)
                    otp_id = str(o.get("otp_id", ""))
                    sms_key = otp_id if otp_id else f"{target_num}_{full_sms}"

                    if sms_key in paid_data:
                        payment_status = "⚠️ <b>Reward already harvested for this packet</b>" if lang == "en" else "⚠️ <b>এই প্যাকেটের রিওয়ার্ড ইতিমধ্যে নেওয়া হয়েছে</b>"
                    else:
                        await update_db_balance(uid, OTP_RATE)
                        add_otp_received(uid)
                        paid_data[sms_key] = {"uid": str(uid), "otp": otp_code}
                        payment_status = f"💵 <b>+{OTP_RATE:.2f} BDT</b> rewarded to wallet!"

                    save_data(paid_data, PAID_SMS_FILE)
                    country_flag, country_name = get_country_info(target_num)
                    service_name = detect_service(full_sms)
                    service_logo = get_service_logo(service_name)

                    msg = (
                        f"{head('DATA RECOVERED', '♻️')}"
                        f"🌍 <b>Origin</b>  ▸ {country_flag} {country_name}\n"
                        f"📱 <b>Service</b> ▸ {service_logo}\n"
                        f"📟 <b>Number</b>  ▸ <code>+{target_num}</code>\n"
                        f"🔑 <b>OTP Key</b> ▸ <code>{html.escape(otp_code)}</code>\n\n"
                        f"📩 <b>FULL PACKET</b>\n"
                        f"<blockquote expandable><code>{html.escape(str(full_sms))}</code></blockquote>\n"
                        f"{payment_status}"
                    )
                    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=main_keyboard(uid))
        else:
            await status_msg.edit_text("❌ Database connectivity failure.")
    except Exception as e:
        try:
            await status_msg.edit_text(f"❌ Error: {str(e)}")
        except Exception:
            await update.message.reply_text(f"❌ Error: {str(e)}")

# ==================== REFER AND EARN SECTION ====================

async def refer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lang = get_user_lang(uid)
    if is_user_banned(uid):
        await update.effective_message.reply_text(LANG_TEXTS[lang]["banned"], parse_mode="HTML", reply_markup=main_keyboard(uid))
        return
    bot_info = await context.bot.get_me()
    referral_link = f"https://t.me/{bot_info.username}?start={uid}"
    successful_refers = get_referral_count(uid)
    total_reward = float(successful_refers) * REFERRAL_PRICE

    if lang == "bn":
        refer_msg = (
            f"{head('REFERRAL HUB', '🎁')}"
            f"<blockquote>👥 সফল রেফারেল ▸ <code>{successful_refers}</code>\n"
            f"🪙 মোট ইনকাম     ▸ <code>{format_balance(total_reward)} BDT</code></blockquote>\n\n"
            f"🔗 <b>আপনার এক্সক্লুসিভ লিংক:</b>\n"
            f"<blockquote expandable><code>{referral_link}</code></blockquote>\n\n"
            f"<i>👑 বন্ধুদের ইনভাইট করুন, প্রতিটি জয়েনে রিওয়ার্ড পান!</i>"
        )
    else:
        refer_msg = (
            f"{head('REFERRAL HUB', '🎁')}"
            f"<blockquote>👥 Total Invites ▸ <code>{successful_refers}</code>\n"
            f"🪙 Total Income  ▸ <code>{format_balance(total_reward)} BDT</code></blockquote>\n\n"
            f"🔗 <b>Your exclusive link:</b>\n"
            f"<blockquote expandable><code>{referral_link}</code></blockquote>\n\n"
            f"<i>👑 Invite friends, harvest rewards on every join!</i>"
        )
    s_ok, s_pri, s_dgr = _styles(lang)
    await update.effective_message.reply_text(
        refer_msg,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([r for r in [
            [b for b in [copy_button("📋 COPY LINK", referral_link),
                         share_button("📤 SHARE", referral_link, "Unlimited free numbers — 24/7 OTP bot 🔥")] if b],
            [make_inline_keyboard_button(BTN[lang]["ref_report"], callback_data=f"my_ref_{uid}", style=s_pri)],
        ] if r])
    )

# ==================== WITHDRAWAL FLOW ====================

async def withdraw_method_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id
    lang = get_user_lang(uid)
    if text in T_CANCEL:
        context.user_data["withdraw_mode"] = None
        await update.message.reply_text(f"🚫 <b>{ub('ABORTED')}</b>", parse_mode="HTML", reply_markup=main_keyboard(uid))
        return
    if text in WITHDRAW_METHOD_MAP:
        context.user_data["withdraw_method"] = WITHDRAW_METHOD_MAP[text]
        context.user_data["withdraw_mode"] = "amount"
        await update.message.reply_text(
            LANG_TEXTS[lang]["withdraw_amount_prompt"].format(min_val=MIN_WITHDRAW),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(uid)
        )
    else:
        await update.message.reply_text("⚠️ Gateway unrecognized!", reply_markup=withdraw_method_keyboard(uid))

async def withdraw_amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id
    lang = get_user_lang(uid)
    if text in T_CANCEL:
        context.user_data["withdraw_mode"] = None
        await update.message.reply_text(f"🚫 <b>{ub('ABORTED')}</b>", parse_mode="HTML", reply_markup=main_keyboard(uid))
        return
    try:
        amount = float(text)
    except Exception:
        await update.message.reply_text("⚠️ Invalid numerical value!", reply_markup=cancel_keyboard(uid))
        return
    balance = get_user(uid)['balance']
    if amount < MIN_WITHDRAW or amount > MAX_WITHDRAW:
        await update.message.reply_text(f"📉 <b>LIMITS:</b> MIN <code>{MIN_WITHDRAW}</code> BDT • MAX <code>{MAX_WITHDRAW}</code> BDT", parse_mode="HTML", reply_markup=cancel_keyboard(uid))
        return
    if amount > balance:
        await update.message.reply_text(f"🚫 <b>{ub('INSUFFICIENT BALANCE')}</b>", parse_mode="HTML", reply_markup=cancel_keyboard(uid))
        return
    context.user_data["withdraw_amount"] = amount
    context.user_data["withdraw_mode"] = "number"
    await update.message.reply_text(
        LANG_TEXTS[lang]["withdraw_number_prompt"],
        parse_mode="HTML",
        reply_markup=cancel_keyboard(uid)
    )

async def withdraw_number_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id
    lang = get_user_lang(uid)
    if text in T_CANCEL:
        context.user_data["withdraw_mode"] = None
        await update.message.reply_text(f"🚫 <b>{ub('ABORTED')}</b>", parse_mode="HTML", reply_markup=main_keyboard(uid))
        return
    if not is_valid_bangladesh_number(text):
        await update.message.reply_text("⚠️ FORMAT CONFLICT! MUST BE <code>017XXXXXXXX</code>", parse_mode="HTML", reply_markup=cancel_keyboard(uid))
        return

    method = context.user_data.get("withdraw_method")
    amount = context.user_data.get("withdraw_amount")
    payment_number = text
    payment_id = generate_payment_id()
    context.user_data["temp_withdraw"] = {
        "method": method, "amount": amount,
        "number": payment_number, "payment_id": payment_id
    }

    s_ok, s_pri, s_dgr = _styles(lang)
    proposal_msg = (
        f"{head('CONFIRM PAYOUT', '💎')}"
        f"<blockquote>💳 Gateway ▸ <code>{method}</code>\n"
        f"📲 Number  ▸ <code>{payment_number}</code>\n"
        f"💰 Amount  ▸ <code>{format_balance(amount)} BDT</code></blockquote>\n\n"
        f"{'কনফার্ম করলে রিকোয়েস্ট অ্যাডমিন কোরে চলে যাবে ⚡' if lang == 'bn' else 'Confirm to forward the request to the admin core ⚡'}"
    )

    await update.message.reply_text(
        proposal_msg,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            make_inline_keyboard_button(BTN[lang]["abort"], callback_data="withdraw_cancel", style=s_dgr),
            make_inline_keyboard_button(BTN[lang]["confirm"], callback_data="withdraw_confirm", style=s_ok)
        ]])
    )

async def process_withdraw_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    await query.answer()
    temp_data = context.user_data.get("temp_withdraw")
    if not temp_data:
        await query.message.reply_text("⚠️ TRANSACTION PACKET EXPIRED.", reply_markup=main_keyboard(uid))
        return

    method = temp_data["method"]
    amount = temp_data["amount"]
    payment_number = temp_data["number"]
    payment_id = temp_data["payment_id"]

    if get_user(uid).get("balance", 0) < amount:
        context.user_data["temp_withdraw"] = None
        context.user_data["withdraw_mode"] = None
        await query.message.edit_text(f"🚫 <b>{ub('INSUFFICIENT BALANCE')}</b>", parse_mode="HTML")
        await context.bot.send_message(uid, "🔹 SECURE CONTROLS RE-ENGAGED:", reply_markup=main_keyboard(uid))
        return

    await update_db_balance(uid, -amount)
    wr = load_withdraw_requests()
    wr[str(payment_id)] = {
        "user_id": uid, "method": method, "amount": amount,
        "number": payment_number, "payment_id": payment_id,
        "status": "pending", "timestamp": datetime.now().isoformat()
    }
    save_withdraw_requests(wr)

    await query.message.edit_text(
        f"{head('PAYOUT QUEUED', '✅')}"
        f"<blockquote>📝 Gateway ▸ <code>{method}</code>\n"
        f"📞 Number  ▸ <code>{payment_number}</code>\n"
        f"💰 Amount  ▸ <code>{format_balance(amount)} BDT</code>\n"
        f"🆔 Hash    ▸ <code>{payment_id}</code></blockquote>\n\n"
        f"⏱️ <i>Approved payouts land within 0-24 hours.</i>",
        parse_mode="HTML"
    )
    await context.bot.send_message(uid, "🎉 <b>REQUEST SUBMITTED!</b>", parse_mode="HTML", reply_markup=main_keyboard(uid))

    admin_msg = (
        f"{head('NEW PAYOUT REQUEST', '📥')}"
        f"🆔 User    ▸ <code>{uid}</code>\n"
        f"📝 Gateway ▸ <code>{method}</code>\n"
        f"📞 Number  ▸ <code>{payment_number}</code>\n"
        f"💰 Amount  ▸ <code>{format_balance(amount)} BDT</code>\n"
        f"🆔 Hash    ▸ <code>{payment_id}</code>"
    )
    admin_kb = InlineKeyboardMarkup([[
        make_inline_keyboard_button(pbtn("❌", "REJECT + REFUND"), callback_data=f"admin_reject_{payment_id}", style="danger"),
        make_inline_keyboard_button(pbtn("✅", "APPROVE & PAY"), callback_data=f"admin_approve_{payment_id}", style="success")
    ]])
    for admin_id in get_all_admins():
        try:
            await context.bot.send_message(admin_id, admin_msg, parse_mode="HTML", reply_markup=admin_kb)
        except Exception as e:
            print(f"Admin notify fail {admin_id}: {e}")

    context.user_data["temp_withdraw"] = None
    context.user_data["withdraw_mode"] = None

async def process_withdraw_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    await query.answer()
    context.user_data["temp_withdraw"] = None
    context.user_data["withdraw_mode"] = None
    await query.message.edit_text(f"❌ <b>{ub('TRANSACTION TERMINATED')}</b>", parse_mode="HTML")
    await context.bot.send_message(uid, "🔹 SECURE CONTROLS RE-ENGAGED:", reply_markup=main_keyboard(uid))

# ==================== ADMIN PANEL - BACKEND CONTROLS ====================

async def admin_approve_withdraw(update, context, payment_id):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    wr = load_withdraw_requests()
    if payment_id not in wr:
        await query.message.reply_text("⚠️ Request not found.")
        return
    rd = wr[payment_id]
    if rd.get("status") != "pending":
        await query.message.reply_text(f"⚠️ Already {rd.get('status')}.")
        return
    uid = rd["user_id"]
    method = rd["method"]
    amount = rd["amount"]
    payment_number = rd["number"]
    wr[payment_id]["status"] = "approved"
    save_withdraw_requests(wr)
    try:
        await send_fx(
            context.bot, uid, effect="party", text=
            f"{head('PAYOUT DISPATCHED', '🎉')}"
            f"<blockquote>📝 Gateway ▸ <code>{method}</code>\n"
            f"📞 Number  ▸ <code>{payment_number}</code>\n"
            f"💰 Credits ▸ <code>{format_balance(amount)} BDT</code></blockquote>\n\n"
            f"✅ <i>Payment completed by admin core. Enjoy!</i>",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await query.message.edit_text(f"✅ <b>{ub('APPROVED')}</b>\n👤 <code>{uid}</code> • 💰 <code>{format_balance(amount)} BDT</code>", parse_mode="HTML")

async def admin_reject_withdraw(update, context, payment_id):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        return
    wr = load_withdraw_requests()
    if payment_id not in wr:
        await query.message.reply_text("⚠️ Request not found.")
        return
    rd = wr[payment_id]
    if rd.get("status") != "pending":
        await query.message.reply_text(f"⚠️ Already {rd.get('status')}.")
        return
    uid = rd["user_id"]
    amount = rd["amount"]
    wr[payment_id]["status"] = "rejected"
    save_withdraw_requests(wr)
    # Refund the amount that was deducted at request time
    await update_db_balance(uid, amount)
    try:
        await context.bot.send_message(
            uid,
            f"{head('PAYOUT DECLINED', '❌')}"
            f"Transaction rejected by admin core.\n<b>Full amount refunded to your wallet.</b>",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await query.message.edit_text(f"❌ <b>{ub('REJECTED')}</b> (refunded)\n👤 <code>{uid}</code> • 💰 <code>{format_balance(amount)} BDT</code>", parse_mode="HTML")

async def admin_pending_payouts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all pending payout requests with instant action buttons."""
    wr = load_withdraw_requests()
    pending = [(pid, rd) for pid, rd in wr.items() if rd.get("status") == "pending"]
    if not pending:
        await update.message.reply_text(
            f"{head('PENDING PAYOUTS', '📥')}⚡ <b>Queue is clean — no pending payouts.</b>",
            parse_mode="HTML",
            reply_markup=system_config_keyboard()
        )
        return
    await update.message.reply_text(
        f"{head('PENDING PAYOUTS', '📥')}📊 <b>{len(pending)} request(s) waiting in queue:</b>",
        parse_mode="HTML"
    )
    for pid, rd in pending[:10]:
        card = (
            f"<blockquote>🆔 User    ▸ <code>{rd['user_id']}</code>\n"
            f"📝 Gateway ▸ <code>{rd['method']}</code>\n"
            f"📞 Number  ▸ <code>{rd['number']}</code>\n"
            f"💰 Amount  ▸ <code>{format_balance(rd['amount'])} BDT</code></blockquote>"
        )
        kb = InlineKeyboardMarkup([[
            make_inline_keyboard_button(pbtn("❌", "REJECT"), callback_data=f"admin_reject_{pid}", style="danger"),
            make_inline_keyboard_button(pbtn("✅", "APPROVE"), callback_data=f"admin_approve_{pid}", style="success")
        ]])
        await update.message.reply_text(card, parse_mode="HTML", reply_markup=kb)
    await update.message.reply_text("☝️ <b>ACTION PANEL READY</b>", parse_mode="HTML", reply_markup=system_config_keyboard())

async def admin_add_balance_start(update, context):
    context.user_data["add_balance_mode"] = True
    context.user_data["remove_balance_mode"] = False
    await update.message.reply_text(f"💎 『 {ub('INJECT BALANCE')} 』\n\nSend target <b>USER ID</b> to inject credits:", parse_mode="HTML")

async def admin_remove_balance_start(update, context):
    context.user_data["remove_balance_mode"] = True
    context.user_data["add_balance_mode"] = False
    await update.message.reply_text(f"💸 『 {ub('PURGE BALANCE')} 』\n\nSend target <b>USER ID</b> to retrieve credits:", parse_mode="HTML")

async def process_add_balance_user(update, context):
    uid_to_add = update.message.text.strip()
    if not uid_to_add.isdigit():
        await update.message.reply_text("❌ Invalid ID.")
        return
    uid_to_add_int = int(uid_to_add)
    if not user_exists(uid_to_add_int):
        await update.message.reply_text("❌ User not found.")
        context.user_data["add_balance_mode"] = False
        return
    context.user_data["pending_add_user"] = uid_to_add_int
    await update.message.reply_text("💵 Send amount to inject:")

async def process_remove_balance_user(update, context):
    uid_to_remove = update.message.text.strip()
    if not uid_to_remove.isdigit():
        await update.message.reply_text("❌ Invalid ID.")
        return
    uid_to_remove_int = int(uid_to_remove)
    if not user_exists(uid_to_remove_int):
        await update.message.reply_text("❌ User not found.")
        context.user_data["remove_balance_mode"] = False
        return
    context.user_data["pending_remove_user"] = uid_to_remove_int
    await update.message.reply_text("💸 Send amount to remove:")

async def process_add_balance_amount(update, context):
    try:
        amount = float(update.message.text.strip())
        if amount <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("❌ Invalid amount.")
        return
    uid = context.user_data.get("pending_add_user")
    if not uid:
        context.user_data["add_balance_mode"] = False
        await update.message.reply_text("⚠️ Session expired.")
        return
    new_balance = await update_db_balance(uid, amount)
    await update.message.reply_text(
        f"{head('BALANCE INJECTED', '✅')}"
        f"🆔 Target   ▸ <code>{uid}</code>\n"
        f"💎 Injected ▸ <code>{format_balance(amount)} BDT</code>\n"
        f"📈 New Bal  ▸ <code>{format_balance(new_balance)} BDT</code>",
        parse_mode="HTML",
        reply_markup=system_config_keyboard()
    )
    try:
        await context.bot.send_message(
            uid,
            f"{head('CREDITS RECEIVED', '🎉')}"
            f"Admin injected <code>{format_balance(amount)} BDT</code> credits to your vault!\n"
            f"💵 Wallet ▸ <code>{format_balance(new_balance)} BDT</code>",
            parse_mode="HTML"
        )
    except Exception:
        pass
    context.user_data["add_balance_mode"] = False
    context.user_data["pending_add_user"] = None

async def process_remove_balance_amount(update, context):
    try:
        amount = float(update.message.text.strip())
        if amount <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("❌ Invalid amount.")
        return
    uid = context.user_data.get("pending_remove_user")
    if not uid:
        context.user_data["remove_balance_mode"] = False
        await update.message.reply_text("⚠️ Session expired.")
        return
    old_balance = get_user(uid).get("balance", 0)
    if amount > old_balance:
        await update.message.reply_text(f"❌ Target balance only has {format_balance(old_balance)} BDT.")
        context.user_data["remove_balance_mode"] = False
        context.user_data["pending_remove_user"] = None
        return
    new_balance = await update_db_balance(uid, -amount)
    await update.message.reply_text(
        f"{head('BALANCE PURGED', '✅')}"
        f"🆔 Target  ▸ <code>{uid}</code>\n"
        f"💸 Purged  ▸ <code>{format_balance(amount)} BDT</code>\n"
        f"📈 New Bal ▸ <code>{format_balance(new_balance)} BDT</code>",
        parse_mode="HTML",
        reply_markup=system_config_keyboard()
    )
    try:
        await context.bot.send_message(
            uid,
            f"{head('CREDITS PURGED', '⚠️')}"
            f"Admin extracted <code>{format_balance(amount)} BDT</code> from your vault.\n"
            f"💵 Wallet ▸ <code>{format_balance(new_balance)} BDT</code>",
            parse_mode="HTML"
        )
    except Exception:
        pass
    context.user_data["remove_balance_mode"] = False
    context.user_data["pending_remove_user"] = None

async def admin_ban_user_start(update, context):
    context.user_data["admin_ban_mode"] = True
    context.user_data["admin_unban_mode"] = False
    await update.message.reply_text(f"⛔ 『 {ub('BAN PROTOCOL')} 』\n\nSend target <b>USER ID</b> to enforce BAN status:", parse_mode="HTML")

async def admin_unban_user_start(update, context):
    context.user_data["admin_unban_mode"] = True
    context.user_data["admin_ban_mode"] = False
    await update.message.reply_text(f"🔓 『 {ub('UNBAN PROTOCOL')} 』\n\nSend target <b>USER ID</b> to clear BAN status:", parse_mode="HTML")

async def process_ban_user(update, context):
    uid_to_ban = update.message.text.strip()
    if not uid_to_ban.isdigit():
        await update.message.reply_text("❌ Invalid ID.")
        return
    uid_to_ban_int = int(uid_to_ban)
    if not user_exists(uid_to_ban_int):
        await update.message.reply_text("❌ User not found.")
        context.user_data["admin_ban_mode"] = False
        return
    if is_user_banned(uid_to_ban_int):
        await update.message.reply_text("⚠️ Node is already blocked.")
        context.user_data["admin_ban_mode"] = False
        return
    ban_user(uid_to_ban_int)
    try:
        await context.bot.send_message(uid_to_ban_int, LANG_TEXTS[get_user_lang(uid_to_ban_int)]["banned"], parse_mode="HTML")
    except Exception:
        pass
    await update.message.reply_text(f"✅ User <code>{uid_to_ban}</code> <b>{ub('BANNED')}</b>", parse_mode="HTML", reply_markup=system_config_keyboard())
    context.user_data["admin_ban_mode"] = False

async def process_unban_user(update, context):
    uid_to_unban = update.message.text.strip()
    if not uid_to_unban.isdigit():
        await update.message.reply_text("❌ Invalid ID.")
        return
    uid_to_unban_int = int(uid_to_unban)
    if not is_user_banned(uid_to_unban_int):
        await update.message.reply_text("⚠️ User node is active.")
        context.user_data["admin_unban_mode"] = False
        return
    unban_user(uid_to_unban_int)
    try:
        await context.bot.send_message(uid_to_unban_int, f"✅ 『 {ub('UNBANNED')} 』\n\n<b>Your access suspension has been lifted. Welcome back!</b>", parse_mode="HTML")
    except Exception:
        pass
    await update.message.reply_text(f"✅ User <code>{uid_to_unban}</code> <b>{ub('UNBANNED')}</b>", parse_mode="HTML", reply_markup=system_config_keyboard())
    context.user_data["admin_unban_mode"] = False

async def show_banned_users_list(update, context):
    banned_list = load_banned_users()
    if not banned_list:
        await update.message.reply_text(f"{head('BAN LIST', '🚫')}⚡ <b>Zero banned nodes. Network is clean.</b>", parse_mode="HTML", reply_markup=system_config_keyboard())
        return
    text = head("BAN LIST", "🚫")
    for i, b_uid in enumerate(banned_list, 1):
        text += f"{i}. <code>{b_uid}</code>\n"
    text += f"\n📊 <b>Total:</b> <code>{len(banned_list)}</code>"
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=system_config_keyboard())

# ==================== ADMIN TEAM MANAGEMENT (MULTI-PANEL SYSTEM) ====================

async def admin_team_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dyn = load_dynamic_admins()
    text = (
        f"{head('ADMIN TEAM', '🛡️')}"
        f"<blockquote>👑 Super Admins ▸ <code>{len(BASE_ADMINS)}</code>\n"
        f"⚡ Panel Admins ▸ <code>{len(dyn)}</code></blockquote>\n\n"
        f"<i>Panel admins get the full control tower (except team management).</i>\n\n"
        f"👇 <b>Manage panel access</b>"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=admin_team_keyboard())

async def admin_add_admin_start(update, context):
    context.user_data["admin_add_mode"] = True
    context.user_data["admin_remove_mode"] = False
    await update.message.reply_text(
        f"➕ 『 {ub('ADD ADMIN')} 』\n\nSend the <b>USER ID</b> you want to promote to panel admin:",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(update.effective_user.id)
    )

async def admin_remove_admin_start(update, context):
    context.user_data["admin_remove_mode"] = True
    context.user_data["admin_add_mode"] = False
    await update.message.reply_text(
        f"➖ 『 {ub('REMOVE ADMIN')} 』\n\nSend the <b>USER ID</b> you want to remove from panel access:",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(update.effective_user.id)
    )

async def process_admin_add(update, context):
    raw = update.message.text.strip()
    if not raw.isdigit():
        await update.message.reply_text("❌ Invalid ID. Numeric only.")
        return
    target = int(raw)
    if is_super_admin(target):
        await update.message.reply_text("⚠️ This node is already a 👑 SUPER ADMIN.")
        context.user_data["admin_add_mode"] = False
        return
    if is_admin(target):
        await update.message.reply_text("⚠️ This user already has panel access.")
        context.user_data["admin_add_mode"] = False
        return
    add_dynamic_admin(target)
    await update.message.reply_text(
        f"{head('ADMIN PROMOTED', '🛡️')}"
        f"🆔 New Admin ▸ <code>{target}</code>\n"
        f"📊 Team Size ▸ <code>{len(get_all_admins())}</code>",
        parse_mode="HTML",
        reply_markup=admin_team_keyboard()
    )
    try:
        await context.bot.send_message(
            target,
            f"{head('PROMOTED TO ADMIN', '🛡️')}"
            f"🎉 <b>Congrats! You now have ADMIN PANEL access.</b>\n"
            f"Type /start and tap <b>🛡️ ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ</b> to enter the control tower.",
            parse_mode="HTML",
            reply_markup=main_keyboard(target)
        )
    except Exception as e:
        print(f"Promote notify fail {target}: {e}")
    context.user_data["admin_add_mode"] = False

async def process_admin_remove(update, context):
    raw = update.message.text.strip()
    if not raw.isdigit():
        await update.message.reply_text("❌ Invalid ID. Numeric only.")
        return
    target = int(raw)
    if is_super_admin(target):
        await update.message.reply_text("🚫 Super admins cannot be removed.")
        context.user_data["admin_remove_mode"] = False
        return
    if not remove_dynamic_admin(target):
        await update.message.reply_text("⚠️ This user is not a panel admin.")
        context.user_data["admin_remove_mode"] = False
        return
    await update.message.reply_text(
        f"{head('ADMIN REMOVED', '✅')}"
        f"🆔 Removed ▸ <code>{target}</code>\n"
        f"📊 Team Size ▸ <code>{len(get_all_admins())}</code>",
        parse_mode="HTML",
        reply_markup=admin_team_keyboard()
    )
    try:
        await context.bot.send_message(
            target,
            f"ℹ️ 『 {ub('ACCESS UPDATE')} 』\n\nYour admin panel access has been revoked.\nYou can keep using the bot as a normal user.",
            parse_mode="HTML",
            reply_markup=main_keyboard(target)
        )
    except Exception as e:
        print(f"Demote notify fail {target}: {e}")
    context.user_data["admin_remove_mode"] = False

async def admin_show_team_list(update, context):
    base = BASE_ADMINS
    dyn = load_dynamic_admins()
    text = head("ADMIN ROSTER", "📋")
    text += "👑 <b>SUPER ADMINS</b>\n"
    for a in base:
        text += f"  ▸ <code>{a}</code>\n"
    text += "\n⚡ <b>PANEL ADMINS</b>\n"
    if dyn:
        for a in dyn:
            text += f"  ▸ <code>{a}</code>\n"
    else:
        text += "  <i>— empty —</i>\n"
    text += f"\n📊 <b>Total:</b> <code>{len(get_all_admins())}</code>"
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=admin_team_keyboard())

# ==================== WELCOME VIDEO ADMIN SETUP ====================

async def admin_video_setup_start(update, context):
    uid = update.effective_user.id
    context.user_data["video_setup_mode"] = True
    context.user_data["video_setup_buffer"] = []
    current = load_welcome_videos()
    await update.message.reply_text(
        f"{head('WELCOME VIDEO SETUP', '🎬')}"
        f"📦 Current saved videos ▸ <code>{len(current)}</code>\n\n"
        f"🎥 এখন একবারে একটি করে ভিডিও পাঠান (সর্বোচ্চ {MAX_WELCOME_VIDEOS}টি)।\n"
        f"নতুন ভিডিওগুলো পুরনোটাকে রিপ্লেস করবে।\n\n"
        f"✅ শেষ হলে <b>{A_DONE}</b> চাপুন",
        parse_mode="HTML",
        reply_markup=video_setup_keyboard(uid)
    )

async def process_video_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.message.text or update.message.caption or "").strip()

    if text == A_DONE:
        vids = context.user_data.get("video_setup_buffer", [])
        save_welcome_videos(vids)
        context.user_data["video_setup_mode"] = False
        context.user_data["video_setup_buffer"] = []
        await update.message.reply_text(
            f"{head('VIDEOS SAVED', '✅')}"
            f"🎬 Saved videos ▸ <code>{len(vids)}</code>\n"
            f"✨ New verified users will now receive them instantly.",
            parse_mode="HTML",
            reply_markup=system_config_keyboard()
        )
        return

    if update.message.video:
        buf = context.user_data.get("video_setup_buffer", [])
        if len(buf) >= MAX_WELCOME_VIDEOS:
            await update.message.reply_text(
                f"⚠️ Max <code>{MAX_WELCOME_VIDEOS}</code> videos allowed. Tap <b>{A_DONE}</b> to save.",
                parse_mode="HTML",
                reply_markup=video_setup_keyboard(uid)
            )
            return
        buf.append(update.message.video.file_id)
        context.user_data["video_setup_buffer"] = buf
        await update.message.reply_text(
            f"✅ <b>Video #{len(buf)} captured!</b>\n<i>Send more or tap {A_DONE} to save.</i>",
            parse_mode="HTML",
            reply_markup=video_setup_keyboard(uid)
        )
        return

    await update.message.reply_text(
        f"⚠️ Send a <b>VIDEO</b> file, or tap <b>{A_DONE}</b> / 🚫 ᴄᴀɴᴄᴇʟ",
        parse_mode="HTML",
        reply_markup=video_setup_keyboard(uid)
    )

# ==================== SERVICE VISIBILITY PANEL (ADMIN SHOW/HIDE UI) ====================

def _service_ctrl_view():
    """Build (text, markup) for the service show/hide control panel."""
    services = get_cached_services()
    hidden = set(load_hidden_services())
    text = (
        f"{head('SERVICE CONTROL', '🎛')}"
        f"🟢 <b>Visible</b> ▸ ইউজার প্যানেলে দেখাবে\n"
        f"🔴 <b>Hidden</b>  ▸ ইউজার প্যানেল থেকে লুকানো\n\n"
        f"👇 <b>টগল করতে সার্ভিসে ট্যাপ করুন</b>"
    )
    rows = []
    if not services:
        text += f"\n\n{DIV}\n⚠️ <i>ক্যাশে কোনো লাইভ সার্ভিস নেই — REFRESH চাপুন।</i>"
    for i, svc in enumerate(services):
        sid = str(svc.get("sid", f"Service {i+1}"))
        is_hidden = sid in hidden
        state = "🔴" if is_hidden else "🟢"
        rows.append([make_inline_keyboard_button(
            f"{state} {service_icon(sid)} {sid}",
            callback_data=f"svctl_{i}",
            style="danger" if is_hidden else "success"
        )])
    rows.append([
        make_inline_keyboard_button(pbtn("🔄", "REFRESH"), callback_data="svctl_refresh", style="success"),
        make_inline_keyboard_button(pbtn("✖️", "CLOSE"), callback_data="svctl_close", style="danger"),
    ])
    return text, InlineKeyboardMarkup(rows)

async def admin_service_panel(update, context):
    if not get_cached_services():
        await _do_liveaccess_fetch()
    text, kb = _service_ctrl_view()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

# ==================== NUMBERS-PER-PICK PANEL (ADMIN 1/2 TOGGLE) ====================

def _numpick_view():
    cur = get_numbers_per_pick()
    text = (
        f"{head('NUMBERS PER PICK', '🔢')}"
        f"⚡ বর্তমান সেটিং ▸ <code>{cur} NUMBER(S)</code>\n\n"
        f"<i>ɢᴇᴛ ɴᴜᴍʙᴇʀ / নাম্বার নিন থেকে ইউজার সার্ভিস সিলেক্ট করলে ঠিক {cur}টা নাম্বার পাবে।</i>\n\n"
        f"👇 <b>নতুন ভ্যালু সিলেক্ট করুন</b>"
    )
    presets = [1, 2, 3, 5, 10, 20, 50, 100]
    btns = [make_inline_keyboard_button(("✅ " if cur == n else "") + f"{n}", callback_data=f"npk_{n}",
                                        style="success" if cur == n else "primary") for n in presets]
    rows = [btns[i:i + 4] for i in range(0, len(btns), 4)]
    rows.append([make_inline_keyboard_button(f"✏️ CUSTOM (unlimited){'' if cur in presets else f' — now {cur}'}", callback_data="npk_custom", style="success")])
    return text, InlineKeyboardMarkup(rows)

async def admin_numpick_panel(update, context):
    text, kb = _numpick_view()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

# ==================== PER-PANEL OTP RATE PANEL (v16.15) ====================

def _panel_rates_view():
    """Admin: set taka-per-OTP for each panel (reward per delivered OTP)."""
    rates = panel_rates()
    lines = [head("PANEL RATES · ৳ PER OTP", "💵").rstrip("\n"),
             f"💰 Default (no custom rate) ▸ <code>{OTP_RATE:.2f} ৳</code>\n"
             f"<i>Set how much taka a user earns per OTP delivered by each panel.</i>\n{DIV_S}"]
    rows = []
    for p in PANELS:
        pid = p["id"]
        cur = rates.get(pid)
        cur_s = f"{float(cur):.2f} ৳" if cur is not None else f"default ({OTP_RATE:.2f} ৳)"
        lines.append(f"🛰️ <b>{html.escape(p['name'])}</b> ▸ <code>{cur_s}</code>")
        rows.append([
            make_inline_keyboard_button("➖ 0.50", callback_data=f"prate_{pid}_dn", style="danger"),
            make_inline_keyboard_button(f"💵 {cur_s}", callback_data=f"prate_{pid}_noop", style="primary"),
            make_inline_keyboard_button("➕ 0.50", callback_data=f"prate_{pid}_up", style="success"),
        ])
        rows.append([
            make_inline_keyboard_button("✏️ Custom rate", callback_data=f"prate_{pid}_custom", style="primary"),
            make_inline_keyboard_button("🔄 Reset to default", callback_data=f"prate_{pid}_reset", style="primary"),
        ])
    rows.append([make_inline_keyboard_button(A_BACK, callback_data="admin_back_core", style="danger")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)

async def admin_panel_rates(update, context):
    text, kb = _panel_rates_view()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

# ==================== PREMIUM EMOJI STUDIO (ADMIN) ====================

def _pe_studio_view(page=0):
    pe_load()
    ui = pe_ui_emojis()
    mapped = sum(1 for e in ui if _pe_norm(e) in _PE["map"])
    state = "🟢 ON" if _PE["enabled"] else "🔴 OFF"
    auto = f"\n⚠️ <i>Auto-disabled by Telegram: <code>{html.escape(str(_PE['auto_disabled'])[:90])}</code></i>" if _PE["auto_disabled"] else ""
    text = (
        f"{head('PREMIUM EMOJI STUDIO', '🎨')}"
        f"<blockquote>🎛️ Engine   ▸ <code>{state}</code>\n"
        f"🧩 Mapped   ▸ <code>{mapped}/{len(ui)} UI icons</code>\n"
        f"🗂️ Total    ▸ <code>{len(_PE['map'])} custom ids</code></blockquote>{auto}\n\n"
        f"<b>⚡ CLONE (সবচেয়ে সহজ):</b>\n"
        f"{num_badge(1)} যে বটের আইকন চান, সেই বটের মেসেজগুলো (MY PROFILE, TRAFFIC, SUPPORT, join prompt…) "
        f"<b>এই বটে Forward</b> করুন\n"
        f"{num_badge(2)} বট লেবেল দেখে আইকন কপি করে নেবে — Wallet ▸ 💳, User ID ▸ 👤, OTP ▸ 💬, Referral ▸ 🎁 …\n"
        f"{num_badge(3)} বাটনের আইকন (📞 📊 👥 ⚙️ ❌ 📁) ফরোয়ার্ডে আসে না — forward করা মেসেজের ইমোজিতে ট্যাপ ▸ "
        f"প্যাক খুলুন ▸ নিচের গ্রিডে টার্গেট আইকন ট্যাপ করে প্যাকের ইমোজিটি পাঠান\n\n"
        f"<i>⚠️ প্রিমিয়াম ইমোজি দেখাতে বটের owner অ্যাকাউন্টে Telegram Premium থাকতে হবে। "
        f"না থাকলে বট অটো plain ইমোজিতে ফিরে যাবে — কিছুই ভাঙবে না।</i>\n\n"
        f"👇 <b>আইকন ট্যাপ করুন = ম্যানুয়াল টার্গেট</b>  (✅ = mapped)"
    )
    per_page = 32
    pages = max(1, (len(ui) + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))
    chunk = ui[page * per_page:(page + 1) * per_page]
    rows, row = [], []
    for e in chunk:
        tick = "✅" if _pe_norm(e) in _PE["map"] else "·"
        # NOTE: no leading-emoji here on purpose → the grid always shows the PLAIN icon
        row.append(make_inline_keyboard_button(f"{tick} {e}", callback_data=f"pe_pick_{_pe_norm(e)}"))
        if len(row) == 4:
            rows.append(row); row = []
    if row:
        rows.append(row)
    nav = []
    if pages > 1:
        nav.append(make_inline_keyboard_button("◀️", callback_data=f"pe_page_{page-1}", style="primary"))
        nav.append(make_inline_keyboard_button(f"{page+1}/{pages}", callback_data="pe_noop"))
        nav.append(make_inline_keyboard_button("▶️", callback_data=f"pe_page_{page+1}", style="primary"))
        rows.append(nav)
    rows.append([
        make_inline_keyboard_button(pbtn("🔴" if _PE["enabled"] else "🟢", "DISABLE" if _PE["enabled"] else "ENABLE"),
                                    callback_data="pe_toggle", style="danger" if _PE["enabled"] else "success"),
        make_inline_keyboard_button(pbtn("🧪", "PREVIEW"), callback_data="pe_preview", style="primary"),
    ])
    rows.append([
        make_inline_keyboard_button(pbtn("🧹", "CLEAR ALL"), callback_data="pe_clear", style="danger"),
        make_inline_keyboard_button(pbtn("✖️", "CLOSE"), callback_data="pe_close"),
    ])
    return text, InlineKeyboardMarkup(rows)

async def admin_emoji_studio(update, context):
    context.user_data["emoji_studio"] = True
    context.user_data["emoji_target"] = None
    text, kb = _pe_studio_view(0)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

async def _pe_capture(update, context):
    """Admin pasted premium emoji while the Studio is open → learn the ids."""
    msg = update.message
    try:
        parsed = msg.parse_entities([MessageEntity.CUSTOM_EMOJI]) if msg.text else msg.parse_caption_entities([MessageEntity.CUSTOM_EMOJI])
    except Exception:
        parsed = {}
    pairs = []
    for ent, plain in parsed.items():
        cid = getattr(ent, "custom_emoji_id", None)
        if cid:
            pairs.append((_pe_norm(plain), str(cid)))
    # a lone custom-emoji *sticker* also works
    if not pairs and msg.sticker and getattr(msg.sticker, "custom_emoji_id", None):
        pairs.append((_pe_norm(msg.sticker.emoji or ""), str(msg.sticker.custom_emoji_id)))
    if not pairs:
        return False

    target = context.user_data.get("emoji_target")
    lines = []
    if target and not _is_forwarded(msg):
        pe_set(target, pairs[0][1])
        context.user_data["emoji_target"] = None
        lines.append(f"🎯 {target} ▸ <code>{pairs[0][1]}</code>  ✅")
    else:
        # CLONE mode: copy icons onto our matching icons using the label next to each one
        lines = pe_clone_apply(pe_clone_pairs(msg))
        if not lines:
            for plain, cid in pairs:
                if plain:
                    pe_set(plain, cid)
                    lines.append(f"{plain} ▸ <code>{cid}</code>  ✅")
    # make sure the fresh ids are real
    try:
        await pe_validate_ids(context.bot)
    except Exception:
        pass
    # if engine was auto-disabled earlier, give it another chance with the new ids
    if _PE["auto_disabled"]:
        pe_toggle(True)

    await msg.reply_text(
        f"{head('ICONS CLONED', '🎨')}" + "\n".join(lines[:40]) +
        f"\n\n<i>মোট {len(_PE['map'])} টি custom id সেভ আছে।</i>",
        parse_mode="HTML"
    )
    # live preview with the freshly cloned icons
    try:
        await _pe_live_preview(context, msg.chat_id, update.effective_user)
    except Exception as e:
        print(f"clone preview fail: {e}")
    if context.user_data.get("emoji_studio"):
        text, kb = _pe_studio_view(0)
        await msg.reply_text(text, parse_mode="HTML", reply_markup=kb)
    return True

async def cloneemoji_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["emoji_studio"] = True
    context.user_data["emoji_target"] = None
    await update.effective_message.reply_text(
        f"{head('EMOJI CLONE', '🎨')}"
        f"{num_badge(1)} অন্য বটের <b>MY PROFILE</b>, <b>TRAFFIC</b>, <b>SUPPORT</b> আর <b>join prompt</b> মেসেজগুলো এখানে <b>Forward</b> করুন\n"
        f"{num_badge(2)} সাথে সাথে আইকন কপি হয়ে যাবে এবং প্রিভিউ দেখাবে\n"
        f"{num_badge(3)} বাকি বাটন-আইকনের জন্য: Admin ▸ SYSTEM CORE ▸ 🎨 PREMIUM EMOJI ▸ আইকন ট্যাপ ▸ ইমোজি পাঠান\n\n"
        f"<i>⚠️ owner অ্যাকাউন্টে Telegram Premium থাকতে হবে — না থাকলে plain ইমোজি দেখাবে।</i>",
        parse_mode="HTML")

async def _pe_live_preview(context, chat_id, user):
    """Show the REAL profile card + main keyboard so the admin sees the cloned icons at once."""
    uid = user.id
    balance = (get_user(uid) or {}).get("balance", 0)
    stats = get_user_stats(uid)
    card = profile_card("en", uid, user.full_name, user.username, balance, stats)
    await context.bot.send_message(chat_id=chat_id, text=card, parse_mode="HTML", reply_markup=profile_actions_kb())
    await context.bot.send_message(chat_id=chat_id, text="👋 Welcome! Please choose an option from the menu below:",
                                   parse_mode="HTML", reply_markup=main_keyboard(uid))

async def _pe_preview(context, chat_id, uid):
    """Send a sample card + keyboard so the admin sees the premium icons live."""
    lang = get_user_lang(uid)
    sample = (
        f"{head('PREVIEW CARD', '🧪')}"
        f"💎 Premium ▸ <code>ACTIVE</code>\n👑 Profile ▸ <code>OK</code>\n💰 Wallet ▸ <code>0.00 BDT</code>\n"
        f"🏆 Rank ▸ <code>#1</code>\n🔑 OTP ▸ <code>123456</code>\n\n"
        f"🟢 WhatsApp  🔵 Facebook  📸 Instagram  🎵 TikTok  ✈️ Telegram"
    )
    kb = InlineKeyboardMarkup([
        [make_inline_keyboard_button("🔵 Facebook", callback_data="pe_noop", style="primary"),
         make_inline_keyboard_button("📸 Instagram", callback_data="pe_noop", style="primary")],
        [make_inline_keyboard_button("🎵 TikTok", callback_data="pe_noop", style="primary"),
         make_inline_keyboard_button("🐦 Twitter", callback_data="pe_noop", style="primary")],
        [make_inline_keyboard_button(BTN[lang]["withdraw"], callback_data="pe_noop", style="success")],
        [make_inline_keyboard_button(pbtn("✖️", "CLOSE"), callback_data="pe_close", style="danger")],
    ])
    await context.bot.send_message(chat_id=chat_id, text=sample, parse_mode="HTML", reply_markup=kb)
    await context.bot.send_message(chat_id=chat_id, text="⌨️ <b>Main keyboard preview</b> 👇", parse_mode="HTML", reply_markup=main_keyboard(uid))

# ==================== MAIN CORE MESSAGE HANDLER ====================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    uid = update.effective_user.id
    text = (update.message.text or update.message.caption or "").strip()
    # premium-icon buttons send their label WITHOUT the emoji → restore canonical label
    text = canon_label(text)
    lang = get_user_lang(uid)

    # Premium Emoji capture:
    #   • Studio open  → any pasted premium emoji is learned
    #   • Studio closed → a FORWARDED message with premium emoji is cloned automatically
    if is_admin(uid) and not context.user_data.get("broadcast_mode") and \
            (context.user_data.get("emoji_studio") or _is_forwarded(update.message)):
        if await _pe_capture(update, context):
            return

    # Abort/Cancel handling
    if text in T_CANCEL:
        context.user_data.clear()
        cancel_txt = f"🚫 『 {ub('ABORTED')} 』\n\nসব অপারেশন বাতিল করা হয়েছে।" if lang == "bn" else f"🚫 『 {ub('ABORTED')} 』\n\nAll operations terminated."
        await update.message.reply_text(cancel_txt, parse_mode="HTML", reply_markup=main_keyboard(uid))
        return

    # User block verification
    if not is_admin(uid) and is_user_banned(uid):
        await update.message.reply_text(LANG_TEXTS[lang]["banned"], parse_mode="HTML", reply_markup=main_keyboard(uid))
        return

    # SUPPORT is always reachable — even before captcha / force-join gates
    if text in T_SUPPORT:
        with AnimTap("support"):
            await support_command(update, context)
        return

    # Config dynamic channels admin interaction
    if context.user_data.get("admin_channel_update_mode") and is_admin(uid):
        context.user_data["admin_channel_update_mode"] = False
        parts = [x.strip() for x in text.split("|")]
        if len(parts) not in (2, 3):
            await update.message.reply_text("❌ invalid format. Format: `channel_id | invite_link | TITLE`", parse_mode="Markdown")
            return
        try:
            ch_id = int(parts[0])
            ch_lnk = parts[1]

            sc = load_sys_config()
            sc["official_channel_id"] = ch_id
            sc["official_channel_link"] = ch_lnk
            if len(parts) == 3 and parts[2]:
                sc["official_channel_title"] = parts[2].upper()
            save_sys_config(sc)

            await update.message.reply_text(
                f"{head('JOIN GATE UPDATED', '✅')}"
                f"🆔 ID  ▸ <code>{ch_id}</code>\n"
                f"🔗 URL ▸ {html.escape(ch_lnk)}",
                parse_mode="HTML",
                reply_markup=system_config_keyboard()
            )
        except Exception as e:
            await update.message.reply_text(f"❌ config failure: {e}")
        return

    # Force join check
    if not is_admin(uid):
        cur_cfg = load_sys_config()
        is_member = await check_channel_membership(context.bot, uid, cur_cfg["official_channel_id"])
        if not is_member:
            await show_join_prompt(update, context, uid)
            return

    # Withdraw flows
    if context.user_data.get("withdraw_mode") == "select_method":
        await withdraw_method_selected(update, context)
        return
    if context.user_data.get("withdraw_mode") == "amount":
        await withdraw_amount_received(update, context)
        return
    if context.user_data.get("withdraw_mode") == "number":
        await withdraw_number_received(update, context)
        return

    # Admin balance operations
    if context.user_data.get("add_balance_mode") and is_admin(uid):
        if context.user_data.get("pending_add_user"):
            await process_add_balance_amount(update, context)
        else:
            await process_add_balance_user(update, context)
        return
    if context.user_data.get("remove_balance_mode") and is_admin(uid):
        if context.user_data.get("pending_remove_user"):
            await process_remove_balance_amount(update, context)
        else:
            await process_remove_balance_user(update, context)
        return

    # Admin ban/unban
    if context.user_data.get("admin_ban_mode") and is_admin(uid):
        await process_ban_user(update, context)
        return
    if context.user_data.get("admin_unban_mode") and is_admin(uid):
        await process_unban_user(update, context)
        return

    # Admin team add/remove modes
    if context.user_data.get("admin_add_mode") and is_super_admin(uid):
        await process_admin_add(update, context)
        return
    if context.user_data.get("admin_remove_mode") and is_super_admin(uid):
        await process_admin_remove(update, context)
        return

    # Welcome video setup collector (before broadcast so setup videos never broadcast)
    if context.user_data.get("video_setup_mode") and is_admin(uid):
        await process_video_setup(update, context)
        return

    # Broadcast engine (must run before text-only routing so media works)
    if context.user_data.get("broadcast_mode") and is_admin(uid):
        context.user_data["broadcast_mode"] = False
        await run_broadcast(update, context, uid)
        return

    # Admin: custom numbers-per-pick (unlimited)
    if context.user_data.get("mode") == "numpick_custom" and text and is_admin(uid):
        context.user_data["mode"] = None
        digits = re.sub(r"\D", "", text)
        if not digits or int(digits) < 1:
            await update.message.reply_text("⚠️ Send a whole number like <code>25</code>.", parse_mode="HTML", reply_markup=main_keyboard(uid))
            return
        set_numbers_per_pick(int(digits))
        t2, kb2 = _numpick_view()
        await update.message.reply_text(f"✅ <b>{int(digits)} number(s) per pick</b> set!", parse_mode="HTML", reply_markup=main_keyboard(uid))
        await update.message.reply_text(t2, parse_mode="HTML", reply_markup=kb2)
        return

    # v16.15: Admin custom per-panel rate input
    _prate_mode = context.user_data.get("mode") or ""
    if _prate_mode.startswith("prate_custom_") and text and is_admin(uid):
        context.user_data["mode"] = None
        pid = _prate_mode.replace("prate_custom_", "")
        try:
            val = float(re.sub(r"[^\d.]", "", text.replace(",", ".")))
        except Exception:
            val = -1
        if val < 0:
            await update.message.reply_text("⚠️ Send a number like <code>1.5</code>", parse_mode="HTML", reply_markup=main_keyboard(uid))
            return
        set_panel_rate(pid, val)
        await update.message.reply_text(f"✅ <b>{pid.upper()}</b> rate set ▸ <code>{val:.2f} ৳ / OTP</code>", parse_mode="HTML", reply_markup=main_keyboard(uid))
        t2, kb2 = _panel_rates_view()
        await update.message.reply_text(t2, parse_mode="HTML", reply_markup=kb2)
        return

    # Custom range setup
    if context.user_data.get("mode") == "custom_range" and text:
        context.user_data["mode"] = None
        range_text = text.strip().upper()
        if not re.search(r'\d', range_text):
            await update.message.reply_text(
                LANG_TEXTS[lang]["invalid_range"],
                parse_mode="HTML",
                reply_markup=main_keyboard(uid)
            )
            return
        await request_queue.put({
            'type': 'process_numbers',
            'update': update,
            'context': context,
            'range_text': range_text,
            'count': 1
        })
        return

    # Everything below needs plain text
    if not text:
        return

    # Core routing logic (Mapped from both English & Bangla buttons)
    if text in T_PROFILE:
        user_data = get_user(uid)
        stats = get_user_stats(uid)
        user = update.effective_user
        with AnimTap("profile"):
          await update.message.reply_text(
            profile_card(lang, uid, user.full_name, user.username or "N/A", user_data.get('balance', 0), stats),
            parse_mode="HTML",
            reply_markup=profile_actions_kb()
        )
        return

    if text in T_BALANCE:
        balance = get_user(uid)['balance']
        s_ok, s_pri, s_dgr = _styles(lang)
        await update.message.reply_text(
            balance_card(lang, balance),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [make_inline_keyboard_button(BTN[lang]["withdraw"], callback_data="withdraw_start", style=s_ok)],
                [close_button(lang)]
            ])
        )
        return

    if text in T_REFER:
        await refer_command(update, context)
        return

    # v16.10 square menu — Switch V2 (info card; V1 stays fully active)
    if text in T_SWITCH_V2:
        await update.message.reply_text(
            f"{head('SWITCH V2', '🔄')}🚧 <b>V2 mode</b> is on the way.\n<i>V1 is fully active — every feature works here.</i>",
            parse_mode="HTML", reply_markup=main_keyboard(uid))
        return

    if text in T_SEARCH_OTP:
        context.user_data["mode"] = "search_otp"
        await update.message.reply_text(
            LANG_TEXTS[lang]["search_otp_prompt"],
            parse_mode="HTML",
            reply_markup=cancel_keyboard(uid)
        )
        return

    if context.user_data.get("mode") == "search_otp":
        context.user_data["mode"] = None
        await request_queue.put({'type': 'search_otp', 'update': update, 'context': context, 'target_num': normalize_number(text)})
        return

    if text in T_2FA:
        await get_2fa_code(update, context)
        return

    if text in T_GET_NUM:
        await react(context.bot, update.effective_chat.id, update.message.message_id, "⚡")
        with AnimTap("get"):
            await show_app_selection(update, context)
        return

    if text in T_TRAFFIC:
        with AnimTap("traffic"):
            await traffic_command(update, context)
        return

    if text in T_MULTI:
        await show_app_selection(update, context, multi=True)
        return

    if context.user_data.get("mode") == "get_2fa":
        await process_2fa_key(update, context)
        return

    if text in T_LEADERBOARD:
        await leaderboard_command(update, context)
        return

    # Language Menu Change Trigger
    if text in T_LANG:
        s_ok, s_pri, s_dgr = _styles(lang)
        keyboard = InlineKeyboardMarkup([
            [
                make_inline_keyboard_button("🇺🇸 ᴇɴɢʟɪꜱʜ ✨", callback_data="set_lang_en", style=s_pri),
                make_inline_keyboard_button("🇧🇩 বাংলা ✨", callback_data="set_lang_bn", style=s_ok)
            ]
        ])
        msg_txt = f"{head('LANGUAGE', '🌐')}🇺🇸 <b>English</b> (US)\n🇧🇩 <b>বাংলা</b> (BD)\n\n👇 <i>আপনার পছন্দের ভাষা সিলেক্ট করুন / Choose your language</i>"
        await update.message.reply_text(msg_txt, parse_mode="HTML", reply_markup=keyboard)
        return

    # ==================== ADMIN NAVIGATION MANAGEMENT ====================
    if text in T_ADMIN and is_admin(uid):
        context.user_data["admin_mode"] = "main"
        await open_admin_tower(update, uid)
        return

    if text == A_EXIT and context.user_data.get("admin_mode"):
        context.user_data["emoji_studio"] = False
        context.user_data["admin_mode"] = None
        context.user_data["admin_team_mode"] = None
        await update.message.reply_text(f"🏠 <b>{ub('PANEL CLOSED')}</b>\nAdmin context terminated.", parse_mode="HTML", reply_markup=main_keyboard(uid))
        return

    if text == A_BACK and is_admin(uid):
        context.user_data["emoji_studio"] = False
        context.user_data["user_management_mode"] = None
        context.user_data["system_config_mode"] = None
        context.user_data["admin_team_mode"] = None
        context.user_data["admin_mode"] = "main"
        await update.message.reply_text(f"🔙 <b>{ub('MASTER GATEWAY')}</b>", parse_mode="HTML", reply_markup=admin_main_keyboard(is_super_admin(uid)))
        return

    if text == A_USER_CENTER and context.user_data.get("admin_mode") == "main" and is_admin(uid):
        context.user_data["user_management_mode"] = "main"
        await update.message.reply_text(
            f"{head('USER CONTROL CENTER', '👥')}"
            f"📡 Broadcast • 🗂 Exports • 🚫 Ban intel\n\n"
            f"👇 <b>Pick an operation</b>",
            parse_mode="HTML",
            reply_markup=user_management_keyboard()
        )
        return

    if text == A_SYSTEM_CORE and context.user_data.get("admin_mode") == "main" and is_admin(uid):
        context.user_data["system_config_mode"] = "main"
        await update.message.reply_text(
            f"{head('SYSTEM CORE', '⚙️')}"
            f"📊 Telemetry • 💎 Vault ops • ⛔ Access control\n\n"
            f"👇 <b>Pick an operation</b>",
            parse_mode="HTML",
            reply_markup=system_config_keyboard()
        )
        return

    if text == A_ADMIN_TEAM and context.user_data.get("admin_mode") == "main" and is_super_admin(uid):
        context.user_data["admin_team_mode"] = "main"
        await admin_team_panel(update, context)
        return

    if text == A_ADD_ADMIN and context.user_data.get("admin_team_mode") and is_super_admin(uid):
        await admin_add_admin_start(update, context)
        return

    if text == A_REMOVE_ADMIN and context.user_data.get("admin_team_mode") and is_super_admin(uid):
        await admin_remove_admin_start(update, context)
        return

    if text == A_ADMIN_LIST and context.user_data.get("admin_team_mode") and is_super_admin(uid):
        await admin_show_team_list(update, context)
        return

    if text == A_STATS and context.user_data.get("system_config_mode") == "main" and is_admin(uid):
        t_n, t_o, s_n, s_o, tot_n, tot_o = get_global_system_stats()
        total_users = len(get_all_users())
        msg = (
            f"{head('CORE TELEMETRY', '📊')}"
            f"<blockquote>✨ <b>TODAY</b>\n"
            f"📱 Nodes ▸ <code>{t_n}</code>  •  🔑 OTPs ▸ <code>{t_o}</code></blockquote>\n"
            f"<blockquote>🔥 <b>LAST 7 DAYS</b>\n"
            f"📱 Nodes ▸ <code>{s_n}</code>  •  🔑 OTPs ▸ <code>{s_o}</code></blockquote>\n"
            f"<blockquote>🌐 <b>ALL TIME</b>\n"
            f"📱 Nodes ▸ <code>{tot_n}</code>  •  🔑 OTPs ▸ <code>{tot_o}</code>\n"
            f"👥 Users ▸ <code>{total_users}</code>  •  🛡️ Admins ▸ <code>{len(get_all_admins())}</code></blockquote>"
        )
        await update.message.reply_text(msg, parse_mode="HTML")
        return

    if text == A_PAYOUTS and context.user_data.get("system_config_mode") == "main" and is_admin(uid):
        await admin_pending_payouts(update, context)
        return

    if text == A_PANELS and is_admin(uid):
        await panels_command(update, context)
        return

    if text == A_EMOJI and context.user_data.get("system_config_mode") == "main" and is_admin(uid):
        await admin_emoji_studio(update, context)
        return

    if text == A_SERVICES and context.user_data.get("system_config_mode") == "main" and is_admin(uid):
        await admin_service_panel(update, context)
        return

    if text == A_NUMPICK and context.user_data.get("system_config_mode") == "main" and is_admin(uid):
        await admin_numpick_panel(update, context)
        return

    # v16.15: per-panel taka-per-OTP rates
    if text == A_PANEL_RATES and context.user_data.get("system_config_mode") == "main" and is_admin(uid):
        await admin_panel_rates(update, context)
        return

    if text == A_VIDEOS and context.user_data.get("system_config_mode") == "main" and is_admin(uid):
        await admin_video_setup_start(update, context)
        return

    if text == A_CLEAR_VIDEOS and context.user_data.get("system_config_mode") == "main" and is_admin(uid):
        save_welcome_videos([])
        await update.message.reply_text(
            f"{head('VIDEOS CLEARED', '🗑')}⚡ <b>All welcome videos removed.</b>\nNew users will skip the video step.",
            parse_mode="HTML",
            reply_markup=system_config_keyboard()
        )
        return

    if text == A_UPDATE_JOIN and context.user_data.get("system_config_mode") == "main" and is_admin(uid):
        context.user_data["admin_channel_update_mode"] = True
        await update.message.reply_text(
            f"{head('UPDATE JOIN GATE', '📣')}"
            f"Send Channel ID, Link and (optional) button TITLE separated by <code>|</code>\n\n"
            f"Example:\n<code>-1003298796462 | https://t.me/example_channel | POPULAR ZONE</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(uid)
        )
        return

    if text == A_INSPECT and is_admin(uid):
        context.user_data["mode"] = "input_user_id"
        await update.message.reply_text(f"🕵️ 『 {ub('USER INSPECT')} 』\n\nInput target <b>TELEGRAM ID</b>:", parse_mode="HTML", reply_markup=cancel_keyboard(uid))
        return

    if context.user_data.get("mode") == "input_user_id" and is_admin(uid):
        target_uid = text.strip()
        if not target_uid.isdigit():
            await update.message.reply_text("❌ INVALID NUMERIC FORMAT!")
            return
        context.user_data["mode"] = None
        stats = get_user_stats(target_uid)
        msg = (
            f"{head('NODE INSPECTION', '🕵️')}"
            f"🆔 Target ▸ <code>{target_uid}</code>\n\n"
            f"<blockquote>✨ <b>TODAY</b>\n📱 {stats['today_numbers']}  •  🔑 {stats['today_otps']}</blockquote>\n"
            f"<blockquote>🔥 <b>7 DAYS</b>\n📱 {stats['last7d_numbers']}  •  🔑 {stats['last7d_otps']}</blockquote>\n"
            f"<blockquote>🌐 <b>ALL TIME</b>\n📱 {stats['total_numbers']}  •  🔑 {stats['total_otps']}</blockquote>"
        )
        await update.message.reply_text(
            msg, parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                make_inline_keyboard_button(pbtn("📂", "DUMP OTP LOGS"), callback_data=f"full_logs_{target_uid}", style="primary")
            ]])
        )
        return

    if text == A_IDS and context.user_data.get("user_management_mode") == "main" and is_admin(uid):
        users = get_all_users()
        if users:
            content = "\n".join(f"{i}. {u}" for i, u in enumerate(users, 1))
            f = io.BytesIO(content.encode())
            f.name = f"ALL_NODES_{len(users)}.txt"
            await update.message.reply_document(document=f, caption=f"👥 <b>Monitored Nodes:</b> <code>{len(users)}</code>", parse_mode="HTML", reply_markup=user_management_keyboard())
        else:
            await update.message.reply_text("No active nodes.", reply_markup=user_management_keyboard())
        return

    if text == A_BALANCES and context.user_data.get("user_management_mode") == "main" and is_admin(uid):
        user_db = load_data(USER_DATA_FILE)
        if user_db:
            total_bal = sum(v.get("balance", 0) for v in user_db.values())
            lines = [f"{i}. {uid_}: {v.get('balance', 0):.2f} BDT" for i, (uid_, v) in enumerate(user_db.items(), 1)]
            content = f"💰 TOTAL VALUE POOL: {total_bal:.2f} BDT\n\n" + "\n".join(lines)
            f = io.BytesIO(content.encode())
            f.name = f"CORES_{total_bal:.0f}.txt"
            await update.message.reply_document(document=f, caption=f"💵 <b>System Vault Pool:</b> <code>{total_bal:.2f} BDT</code>", parse_mode="HTML", reply_markup=user_management_keyboard())
        else:
            await update.message.reply_text("Telemetry empty.", reply_markup=user_management_keyboard())
        return

    if text == A_BANLIST and is_admin(uid):
        await show_banned_users_list(update, context)
        return

    if text == A_BAN and context.user_data.get("system_config_mode") == "main" and is_admin(uid):
        await admin_ban_user_start(update, context)
        return

    if text == A_UNBAN and context.user_data.get("system_config_mode") == "main" and is_admin(uid):
        await admin_unban_user_start(update, context)
        return

    if text == A_INJECT and context.user_data.get("system_config_mode") == "main" and is_admin(uid):
        await admin_add_balance_start(update, context)
        return

    if text == A_PURGE and context.user_data.get("system_config_mode") == "main" and is_admin(uid):
        await admin_remove_balance_start(update, context)
        return

    # ==================== BROADCAST TRIGGER ====================
    if text == A_BROADCAST and is_admin(uid):
        context.user_data["broadcast_mode"] = True
        await update.message.reply_text(
            f"{head('BROADCAST ENGINE', '📡')}"
            f"💬 এখন আপনি যা পাঠাবেন — তা সকল ইউজারের কাছে প্রিমিয়াম ক্যাপশনসহ পৌঁছে যাবে।\n\n"
            f"✨ রেঞ্জ (যেমন: <code>237XXX</code>) থাকলে অটো ক্লিক-টু-কপি হবে।\n"
            f"🖼️ টেক্সট, ফটো, ভিডিও, ডকুমেন্ট — সব সাপোর্টেড।",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(uid)
        )
        return

    # Fallback response (Dynamic Language UI options)
    fallback_txt = (
        f"{head('MODULE SELECT', '💠')}👇 নিচের মেনু থেকে সার্ভিস সিলেক্ট করুন"
        if lang == "bn" else
        f"{head('MODULE SELECT', '💠')}👇 Pick a module from the menu below"
    )
    await update.message.reply_text(fallback_txt, parse_mode="HTML", reply_markup=main_keyboard(uid))

# ==================== BROADCAST ENGINE (PRO) ====================

def format_broadcast_caption(caption_text):
    if not caption_text:
        return f"{BRAND_LINE}\n{DIV}\n\n<blockquote>📢 <b>ADMIN NOTICE</b></blockquote>"
    escaped = html.escape(str(caption_text))
    formatted = re.sub(r'(\d{3,}[xX]{3,})', r'<code>\1</code>', escaped)
    return f"{BRAND_LINE}\n{DIV}\n\n<blockquote>📢 <b>ADMIN NOTICE</b></blockquote>\n\n{formatted}"

async def run_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, uid):
    msg = update.message
    user_db = load_data(USER_DATA_FILE)
    all_uids = list(user_db.keys())
    if not all_uids:
        await msg.reply_text("❌ পাঠানোর জন্য কোনো ইউজার পাওয়া যায়নি!", reply_markup=main_keyboard(uid))
        return

    success_ids, fail_ids = [], []
    status_msg = await msg.reply_text(
        f"🚀 <b>ব্রডকাস্ট শুরু হয়েছে...</b>\n🎯 টার্গেট: <code>{len(all_uids)}</code> জন ইউজার।",
        parse_mode="HTML"
    )

    for user_id_str in all_uids:
        try:
            target_id = int(user_id_str)
            caption = format_broadcast_caption(msg.caption) if msg.caption else None
            pm = "HTML" if caption else None
            if msg.text:
                await context.bot.send_message(chat_id=target_id, text=format_broadcast_caption(msg.text), parse_mode="HTML")
            elif msg.photo:
                await context.bot.send_photo(chat_id=target_id, photo=msg.photo[-1].file_id, caption=caption, parse_mode=pm)
            elif msg.video:
                await context.bot.send_video(chat_id=target_id, video=msg.video.file_id, caption=caption, parse_mode=pm)
            elif msg.document:
                await context.bot.send_document(chat_id=target_id, document=msg.document.file_id, caption=caption, parse_mode=pm)
            elif msg.audio:
                await context.bot.send_audio(chat_id=target_id, audio=msg.audio.file_id, caption=caption, parse_mode=pm)
            elif msg.voice:
                await context.bot.send_voice(chat_id=target_id, voice=msg.voice.file_id, caption=caption, parse_mode=pm)
            elif msg.animation:
                await context.bot.send_animation(chat_id=target_id, animation=msg.animation.file_id, caption=caption, parse_mode=pm)
            elif msg.sticker:
                await context.bot.send_sticker(chat_id=target_id, sticker=msg.sticker.file_id)
            else:
                try:
                    await context.bot.copy_message(chat_id=target_id, from_chat_id=msg.chat_id, message_id=msg.message_id)
                except Exception:
                    await context.bot.send_message(
                        chat_id=target_id,
                        text=f"{BRAND_LINE}\n{DIV}\n\n📢 <b>ADMIN NOTICE</b>\n\nনতুন আপডেট এসেছে। চেক করুন।",
                        parse_mode="HTML"
                    )
            success_ids.append(user_id_str)
        except Exception as e:
            print(f"Broadcast fail to {user_id_str}: {e}")
            fail_ids.append(user_id_str)
        await asyncio.sleep(0.05)

    report_text = (
        f"{head('BROADCAST REPORT', '✅')}"
        f"<blockquote>✅ DELIVERED ▸ <code>{len(success_ids)}</code> USERS</blockquote>\n"
        f"<blockquote>❌ FAILED    ▸ <code>{len(fail_ids)}</code> USERS</blockquote>"
    )
    try:
        await status_msg.delete()
    except Exception:
        pass
    await context.bot.send_message(chat_id=uid, text=report_text, parse_mode="HTML", reply_markup=main_keyboard(uid))
    random_suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    if success_ids:
        s_file = io.BytesIO(("\n".join(success_ids)).encode())
        s_file.name = f"SUCCESS_{random_suffix}.txt"
        await context.bot.send_document(chat_id=uid, document=s_file, caption="✅ Success User List")
    if fail_ids:
        f_file = io.BytesIO(("\n".join(fail_ids)).encode())
        f_file.name = f"FAILED_{random_suffix}.txt"
        await context.bot.send_document(chat_id=uid, document=f_file, caption="❌ Failed User List")

# ==================== SLASH COMMAND HANDLERS ====================

async def get1number_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_app_selection(update, context)

async def multi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_app_selection(update, context, multi=True)

async def searchotp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lang = get_user_lang(uid)
    context.user_data["mode"] = "search_otp"
    await update.message.reply_text(
        LANG_TEXTS[lang]["search_otp_prompt"],
        parse_mode="HTML",
        reply_markup=cancel_keyboard(uid)
    )

async def range_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    context.user_data["mode"] = "custom_range"
    await update.message.reply_text(LANG_TEXTS["en"]["custom_range_prompt"], parse_mode="HTML", reply_markup=cancel_keyboard(uid))

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lang = get_user_lang(uid)
    balance = get_user(uid)['balance']
    s_ok, s_pri, s_dgr = _styles(lang)
    await update.message.reply_text(
        balance_card(lang, balance),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            make_inline_keyboard_button(BTN[lang]["withdraw"], callback_data="withdraw_start", style=s_pri)
        ]])
    )

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lang = get_user_lang(uid)
    user_data = get_user(uid)
    stats = get_user_stats(uid)
    user = update.effective_user
    _pm = await update.message.reply_text(
        profile_card(lang, uid, user.full_name, user.username or "N/A", user_data.get('balance', 0), stats),
        parse_mode="HTML",
        reply_markup=profile_actions_kb()
    )

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(
        f"🗝️ 『 {ub('YOUR NODE ID')} 』\n\n<code>{uid}</code>",
        parse_mode="HTML"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (
        f"{head('HELP', '❓')}"
        f"📞 /get1number — Get a number\n"
        f"🎰 /multi — Multi numbers\n"
        f"🛠 /range — Custom range\n"
        f"🔍 /searchotp — Search OTP\n"
        f"🔐 /get2fa — 2FA code\n"
        f"📊 /traffic — Traffic (last 1 hour)\n"
        f"📁 /stock — Live stock\n"
        f"👤 /profile — My profile\n"
        f"💳 /balance — Balance & withdraw\n"
        f"🎁 /refer — Refer link\n"
        f"📜 /history — My numbers & OTP status\n"
        f"🎁 /daily — Daily bonus (streak x7)\n"
        f"🏆 /leaderboard — Leaderboard\n"
        f"👥 /support — Support\n"
        f"{DIV}"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_keyboard(uid))

async def open_admin_tower(update: Update, uid):
    cached = get_cached_services()
    svc_info = f"{len(filter_visible_services(cached))}/{len(cached)} LIVE" if cached else "—"
    await update.message.reply_text(
        f"{head('ADMIN CONTROL TOWER', '👑')}"
        f"<blockquote>🛡️ Access    ▸ <code>{'SUPER ROOT' if is_super_admin(uid) else 'PANEL ADMIN'}</code>\n"
        f"⚡ Modules   ▸ <code>ALL UNLOCKED</code>\n"
        f"📡 Broadcast ▸ <code>READY</code>\n"
        f"💎 Vault Ops ▸ <code>ARMED</code>\n"
        f"🎛️ Services  ▸ <code>{svc_info}</code>\n"
        f"🔢 Per Pick  ▸ <code>{get_numbers_per_pick()} NODE(S)</code>\n"
        f"🎬 Videos    ▸ <code>{len(load_welcome_videos())} SAVED</code></blockquote>\n\n"
        f"🎯 <b>Select a control module 👇</b>",
        parse_mode="HTML",
        reply_markup=admin_main_keyboard(is_super_admin(uid))
    )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    context.user_data["admin_mode"] = "main"
    await open_admin_tower(update, uid)

# ==================== START FLOW & CALLBACK INTERFACE ====================

def join_keyboard():
    cur_cfg = load_sys_config()
    title = str(cur_cfg.get("official_channel_title") or DEFAULT_CHANNEL_TITLE).upper()
    return InlineKeyboardMarkup([
        [make_inline_keyboard_button(f"📢 Join Channel: {title}", url=cur_cfg["official_channel_link"])],
        [make_inline_keyboard_button(f"📢 Join Group: {OTP_GROUP_TITLE}", url=OTP_GROUP_JOIN_LINK)],
        [make_inline_keyboard_button(f"📢 Join Channel: {PARTNER_TITLE}", url=_get_h_lnk())],
        [make_inline_keyboard_button(BTN["en"]["verify"], callback_data="verify_join")],
    ])

async def show_join_prompt(update_or_query, context, uid, force_new=False, failed=False):
    text = "⚠️ <b>Please join our channels/groups to use the bot!</b>"
    if failed:
        text += "\n\n❌ <i>You haven't joined yet — join all and tap Check Joined again.</i>"
    keyboard = join_keyboard()
    if force_new:
        await context.bot.send_message(chat_id=uid, text=text, parse_mode="HTML", reply_markup=keyboard)
    elif isinstance(update_or_query, Update) and update_or_query.message:
        await update_or_query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        try:
            await update_or_query.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            await context.bot.send_message(chat_id=uid, text=text, parse_mode="HTML", reply_markup=keyboard)

WELCOME_TEXT = "👋 <b>Welcome!</b> Please choose an option from the menu below:"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    uid_str = str(uid)
    existing_data = load_data(USER_DATA_FILE)
    is_new_user = uid_str not in existing_data
    user_info = get_user(uid)
    update_user_profile_info(uid, update.effective_user.full_name, update.effective_user.username)

    args = context.args
    if args and is_new_user:
        param = args[0]
        if is_range_request(param):
            await request_queue.put({'type': 'auto_number', 'update': update, 'context': context, 'range_text': param})
            return
        elif is_referral_request(param):
            try:
                referrer_id = int(param)
                if referrer_id != uid and str(referrer_id) in existing_data:
                    context.user_data["pending_refer"] = referrer_id
            except Exception as e:
                print(f"Deep link referral mapping error: {e}")

    chat_id = update.effective_chat.id
    set_user_verified(uid, True)   # no captcha / language gate in this design — join-gate only

    cur_cfg = load_sys_config()
    joined = is_admin(uid) or await check_channel_membership(context.bot, uid, cur_cfg["official_channel_id"])
    await react(context.bot, chat_id, update.message.message_id, "❤️")
    if joined:
        await send_fx(context.bot, chat_id, WELCOME_TEXT, effect="heart", reply_markup=main_keyboard(uid))
    else:
        await show_join_prompt(update, context, uid)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = str(update.callback_query.data or "") if update.callback_query else ""
    if data.startswith(ANIM_SKIP_CB):
        return await _button_callback_core(update, context)
    with AnimTap(label=_cb_label(data), kind="cb"):
        return await _button_callback_core(update, context)

async def _button_callback_core(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    data = query.data
    await query.answer()

    if not is_admin(uid) and is_user_banned(uid):
        await query.edit_message_text(LANG_TEXTS[get_user_lang(uid)]["banned"], parse_mode="HTML")
        return

    # Language Config logic
    if data == "set_lang_en":
        user_info = get_user(uid)
        had_lang = user_info.get("lang") is not None
        set_user_lang(uid, "en")

        if had_lang:
            # morph the picker into a confirmation, then slide the refreshed keyboard in
            await _safe_edit(query.message, f"🟢 <b>{ub('LANGUAGE UPDATED')}</b> ▸ English 🇺🇸")
            await _typing(context, uid, 0.3)
            await context.bot.send_message(chat_id=uid, text=welcome_card("en", query.from_user.first_name), parse_mode="HTML", reply_markup=main_keyboard(uid))
        else:
            if not is_user_verified(uid) and not is_admin(uid):
                # language card → captcha card (same message, zero clutter)
                await send_verification_captcha(query.message.chat_id, context, uid, edit_msg=query.message)
            else:
                await show_join_prompt(query, context, uid)
        return

    if data == "set_lang_bn":
        user_info = get_user(uid)
        had_lang = user_info.get("lang") is not None
        set_user_lang(uid, "bn")

        if had_lang:
            # morph the picker into a confirmation, then slide the refreshed keyboard in
            await _safe_edit(query.message, f"🟢 <b>{ub('LANGUAGE UPDATED')}</b> ▸ বাংলা 🇧🇩")
            await _typing(context, uid, 0.3)
            await context.bot.send_message(chat_id=uid, text=welcome_card("bn", query.from_user.first_name), parse_mode="HTML", reply_markup=main_keyboard(uid))
        else:
            if not is_user_verified(uid) and not is_admin(uid):
                # language card → captcha card (same message, zero clutter)
                await send_verification_captcha(query.message.chat_id, context, uid, edit_msg=query.message)
            else:
                await show_join_prompt(query, context, uid)
        return

    # Security Emoji Verification Checker
    if data.startswith("captcha_opt_"):
        idx = int(data.replace("captcha_opt_", ""))
        opts = context.user_data.get("captcha_options", [])
        correct_ans = context.user_data.get("captcha_correct", "")

        if not opts or idx >= len(opts):
            await query.edit_message_text("❌ Verification session expired. Please type /start again.")
            return

        selected = opts[idx]
        if selected == correct_ans:
            set_user_verified(uid, True)
            # captcha card → "VERIFIED" flash (in place), short beat, then next step
            await _safe_edit(
                query.message,
                f"{head('VERIFIED', '✅')}<b>Security check passed!</b>"
            )
            await _typing(context, uid, 0.45)
            # Fire admin-configured starter videos to the freshly verified user
            sent_videos = await send_welcome_videos(uid, context, get_user_lang(uid))
            if sent_videos:
                # videos pushed the chat down → post join prompt as a fresh message
                await show_join_prompt(query, context, uid, force_new=True)
            else:
                # no videos → morph the verified card straight into the join prompt
                await show_join_prompt(query, context, uid)
        else:
            # same message, new sequence, soft error banner (no spam)
            await send_verification_captcha(query.message.chat_id, context, uid, edit_msg=query.message, retry=True)
        return

    # Verify Join / Dynamic membership checker
    if data == "verify_join":
        lang = get_user_lang(uid)
        cur_cfg = load_sys_config()

        is_member = await check_channel_membership(context.bot, uid, cur_cfg["official_channel_id"])
        if not is_member and not is_admin(uid):
            await show_join_prompt(query, context, uid, failed=True)
            return
        set_user_verified(uid, True)

        pending_ref = context.user_data.get("pending_refer")
        if pending_ref:
            try:
                existing_data = load_data(USER_DATA_FILE)
                if str(pending_ref) in existing_data:
                    current_count = get_referral_count(pending_ref)
                    new_count = current_count + 1
                    update_referral_count(pending_ref, new_count)
                    await update_db_balance(pending_ref, REFERRAL_PRICE)
                    log_global_activity(pending_ref, "REFERRAL_JOINED", {"referred_user": uid})
                    try:
                        msg = (
                            f"{head('NODE REPLICATED', '🎉')}"
                            f"<blockquote>🗝️ New ID   ▸ <code>{uid}</code>\n"
                            f"💰 Reward   ▸ <code>{format_balance(REFERRAL_PRICE)} BDT</code>\n"
                            f"👥 Nodes    ▸ <code>{new_count}</code></blockquote>"
                        )
                        await context.bot.send_message(pending_ref, msg, parse_mode="HTML")
                    except Exception:
                        pass
            except Exception as e:
                print(f"Error executing pending referral: {e}")
            context.user_data["pending_refer"] = None

        # join card → "Thanks for joining" → menu (reference flow)
        await _safe_edit(query.message, "✅ <b>Thanks for joining! You can now use the bot.</b>")
        await _typing(context, uid, 0.2)
        await send_fx(context.bot, uid, WELCOME_TEXT, effect="party", reply_markup=main_keyboard(uid))
        # admin-configured starter videos (sent once, right after the first successful join)
        await send_welcome_videos(uid, context, "en")
        return

    # Generic card actions
    if data == "ui_close":
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    if data == "open_refer":
        await refer_command(update, context)
        return

    if data in ("traffic_refresh", "traffic_view"):
        await _safe_edit(query.message, traffic_card(), traffic_actions_kb())
        return

    if data in ("stock_view", "stock_refresh"):
        if data == "stock_refresh" or not get_cached_services():
            await _do_liveaccess_fetch()
        await _safe_edit(query.message, stock_card(), stock_actions_kb())
        return

    if data == "profile_refresh":
        lang = get_user_lang(uid)
        user_data = get_user(uid)
        stats = get_user_stats(uid)
        u = query.from_user
        await _safe_edit(
            query.message,
            profile_card(lang, uid, u.full_name, u.username or "N/A", user_data.get('balance', 0), stats),
            profile_actions_kb()
        )
        return

    # PREMIUM EMOJI STUDIO (ADMIN)
    if data.startswith("pe_"):
        if not is_admin(uid):
            return
        action = data[3:]
        if action == "noop":
            return
        if action == "close":
            context.user_data["emoji_studio"] = False
            context.user_data["emoji_target"] = None
            try:
                await query.message.delete()
            except Exception:
                pass
            return
        if action == "toggle":
            pe_toggle()
        elif action == "clear":
            pe_load(); _PE["map"].clear(); pe_save()
        elif action == "preview":
            await _pe_preview(context, uid, uid)
            return
        elif action.startswith("page_"):
            try:
                pg = int(action[5:])
            except Exception:
                pg = 0
            text, kb = _pe_studio_view(pg)
            await _safe_edit(query.message, text, kb)
            return
        elif action.startswith("pick_"):
            target = action[5:]
            context.user_data["emoji_studio"] = True
            context.user_data["emoji_target"] = target
            cur = _PE["map"].get(target)
            await context.bot.send_message(
                chat_id=uid,
                text=(f"🎯 <b>Target</b> ▸ {target}" + (f"  (current: <code>{cur}</code>)" if cur else "") +
                      f"\n\n👉 এখন <b>১টি প্রিমিয়াম ইমোজি</b> পাঠান — সেটিই {target} এর জায়গায় বসবে।"),
                parse_mode="HTML"
            )
            return
        text, kb = _pe_studio_view(0)
        await _safe_edit(query.message, text, kb)
        return

    # SERVICE VISIBILITY CONTROL (ADMIN TOGGLE)
    if data.startswith("svctl_"):
        if not is_admin(uid):
            return
        action = data.replace("svctl_", "")
        if action == "refresh":
            await _do_liveaccess_fetch()
        elif action == "close":
            try:
                await query.message.delete()
            except Exception:
                pass
            return
        else:
            services = get_cached_services()
            try:
                idx = int(action)
            except Exception:
                idx = -1
            if 0 <= idx < len(services):
                sid = str(services[idx].get("sid", ""))
                toggle_service_hidden(sid)
        text, kb = _service_ctrl_view()
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass
        return

    # NUMBERS-PER-PICK CONTROL (ADMIN, unlimited)
    if data.startswith("pnl_") and is_admin(query.from_user.id):
        try:
            if data == "pnl_close":
                await query.message.delete()
                return
            if data == "pnl_refresh":
                await _do_liveaccess_fetch()
                text, kb = panels_overview()
            else:
                text, kb = panel_detail(data[6:])
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)
        except Exception as e:
            if "not modified" not in str(e).lower():
                print(f"[panels] view error: {e}")
        return

    # v16.15: per-panel taka-per-OTP rate controls
    if data.startswith("prate_"):
        if not is_admin(uid):
            return
        try:
            _, pid, act = data.split("_", 2)
        except Exception:
            return
        if act == "noop":
            try:
                await query.answer(f"{pid.upper()} ▸ {panel_rate(pid):.2f} ৳ / OTP", show_alert=True)
            except Exception:
                pass
            return
        if act == "up":
            set_panel_rate(pid, panel_rate(pid) + 0.50)
        elif act == "dn":
            set_panel_rate(pid, panel_rate(pid) - 0.50)
        elif act == "reset":
            cfg = load_sys_config() or {}
            pr = cfg.get("panel_rates") if isinstance(cfg.get("panel_rates"), dict) else {}
            pr.pop(str(pid), None)
            cfg["panel_rates"] = pr
            save_sys_config(cfg)
        elif act == "custom":
            context.user_data["mode"] = f"prate_custom_{pid}"
            try:
                await query.message.edit_text(
                    f"✏️ <b>Custom rate for {html.escape(pid.upper())}</b>\n\nSend the new taka-per-OTP value, e.g. <code>1.5</code>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[make_inline_keyboard_button(A_BACK, callback_data="admin_back_core", style="danger")]]))
            except Exception:
                pass
            return
        text, kb = _panel_rates_view()
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass
        try:
            await query.answer(f"✅ {pid.upper()} ▸ {panel_rate(pid):.2f} ৳ / OTP", show_alert=True)
        except Exception:
            pass
        return

    if data == "npk_custom":
        if not is_admin(uid):
            return
        context.user_data["mode"] = "numpick_custom"
        await query.message.reply_text(
            f"{head('CUSTOM PER PICK', '✏️')}🔢 কয়টা নাম্বার দিতে চান, সংখ্যাটা লিখে পাঠান (যেকোনো সংখ্যা, লিমিট নেই)\n<i>Example: <code>25</code></i>",
            parse_mode="HTML", reply_markup=cancel_keyboard(uid))
        return
    if data.startswith("npk_"):
        if not is_admin(uid):
            return
        try:
            n = int(data.replace("npk_", ""))
        except Exception:
            n = 1
        set_numbers_per_pick(n)
        text, kb = _numpick_view()
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass
        try:
            await query.answer(f"✅ {n} number(s) per pick set!", show_alert=True)
        except Exception:
            pass
        return

    # LIVEACCESS — SERVICE SELECTION
    if data.startswith("svc_"):
        idx = int(data.replace("svc_", ""))
        services = context.user_data.get("la_services", [])
        if not services:
            services = filter_visible_services(get_cached_services())
            context.user_data["la_services"] = services
        if idx >= len(services):
            try:
                await query.answer("Session expired.", show_alert=True)
            except Exception:
                pass
            return
        svc = services[idx]
        sid = svc.get("sid", "Service")
        ranges = svc.get("ranges", [])
        if not ranges:
            try:
                await query.answer("No telemetry paths available.", show_alert=True)
            except Exception:
                pass
            return

        context.user_data["la_svc_idx"] = idx
        context.user_data["la_sid"] = sid
        context.user_data["la_ranges"] = ranges
        countries = _group_ranges_by_country(ranges)
        context.user_data["la_countries"] = countries
        keyboard = _build_countries_keyboard(countries, idx, uid)
        pick_txt = f"📍 <b>Select a country for {html.escape(str(sid).upper())}:</b>"
        await query.message.edit_text(
            pick_txt,
            parse_mode="HTML",
            reply_markup=keyboard
        )
        return

    # WORLD-CLASS PACK — quick pick / change / release / my numbers / daily bonus
    if data == "quick_pick":
        qp = quick_pick_get(uid)
        if not qp or not qp.get("range"):
            try:
                await query.answer("No previous pick yet.", show_alert=True)
            except Exception:
                pass
            return
        context.user_data["la_sid"] = qp.get("sid", "")
        mx = get_max_active()
        if mx and count_active_numbers(uid) >= mx:
            try:
                await query.answer(f"⚠️ Limit: {mx} active numbers. Release one first.", show_alert=True)
            except Exception:
                pass
            return
        if get_numbers_per_pick() >= 2:
            await request_queue.put({'type': 'process_numbers', 'update': update, 'context': context,
                                     'range_text': qp["range"], 'count': get_numbers_per_pick()})
            return
        asyncio.create_task(fast_allocate_number(query, context, qp["range"], qp.get("sid", "")))
        return
    if data.startswith("chg_") or data.startswith("rel_"):
        num = normalize_number(data.split("_", 1)[1])
        info = active_numbers.get(num) or {}
        if info and str(info.get("uid")) != str(uid):
            try:
                await query.answer("Not your number.", show_alert=True)
            except Exception:
                pass
            return
        rng = info.get("range") or last_range.get(uid) or (load_range_db().get(num) or {}).get("range")
        release_number(num, uid)
        if data.startswith("rel_"):
            try:
                await query.message.edit_text(f"{head('RELEASED', '🗑️')}📟 <code>+{num}</code> released — no longer monitored.\n{DIV}", parse_mode="HTML",
                                              reply_markup=InlineKeyboardMarkup([[make_inline_keyboard_button(BTN[get_user_lang(uid)]["back"], callback_data="back_services")]]))
            except Exception:
                pass
            return
        if not rng:
            try:
                await query.answer("Range unknown — use GET NUMBER.", show_alert=True)
            except Exception:
                pass
            return
        try:
            await query.message.edit_text(f"🔁 <b>Changing number…</b> <code>{html.escape(str(rng))}</code>", parse_mode="HTML")
        except Exception:
            pass
        asyncio.create_task(fast_allocate_number(query, context, rng, context.user_data.get("la_sid") or (quick_pick_get(uid) or {}).get("sid", "")))
        return
    if data == "my_numbers":
        await _safe_edit(query.message, my_numbers_card(uid), my_numbers_kb())
        return
    if data == "daily_bonus":
        ok, amt, streak, nxt = await claim_daily_bonus(uid)
        try:
            await query.answer(f"🎁 +{amt:.2f} ৳ · streak day {streak}" if ok else f"⏳ Next bonus in {nxt // 3600}h {(nxt % 3600) // 60}m", show_alert=True)
        except Exception:
            pass
        if ok:
            try:
                await send_fx(query.get_bot(), uid, daily_card(ok, amt, streak, nxt), effect="party")
            except Exception:
                pass
        return

    # AUTO ENGINE — COUNTRY SELECTION → range picker (or straight to allocation when only one range)
    if data.startswith("cty_") or data.startswith("rauto_"):
        try:
            ci = int(data.split("_", 1)[1])
        except Exception:
            ci = -1
        countries = context.user_data.get("la_countries", [])
        ranges = context.user_data.get("la_ranges", [])
        g = countries[ci] if 0 <= ci < len(countries) else None
        idxs = [i for i in (g or {}).get("idxs", []) if i < len(ranges)]
        if not g or not idxs:
            try:
                await query.answer("Session expired. Re-open GET NUMBER.", show_alert=True)
            except Exception:
                pass
            return
        if data.startswith("cty_") and len(idxs) > 1 and context.user_data.get("multi_mode"):
            # MULTI burst keeps its range picker; the normal GET NUMBER flow goes straight to allocation
            sid = context.user_data.get("la_sid", "")
            txt = (f"{service_icon(sid)} <b>{html.escape(str(sid).upper())}</b> ▸ {g['flag']} <b>{html.escape(g['country'])}</b>\n\n"
                   f"🛰️ <b>Select a range:</b>  <i>({len(idxs)} auto-added · sorted by live traffic)</i>")
            await query.message.edit_text(
                txt, parse_mode="HTML",
                reply_markup=_build_ranges_keyboard(g, ranges, context.user_data.get("la_svc_idx", 0), ci, uid)
            )
            return
        # normal GET NUMBER: straight to the best range of this country — pre-sorted hot → traffic → live, first wins
        data = f"rng_{idxs[0]}"

    # LIVEACCESS — RANGE SELECTION (SINGLE or MULTI-BURST mode)
    if data.startswith("rng_"):
        idx = int(data.replace("rng_", ""))
        ranges = context.user_data.get("la_ranges", [])
        if idx >= len(ranges):
            try:
                await query.answer("Timeout error. Re-try.", show_alert=True)
            except Exception:
                pass
            return
        range_text = ranges[idx]
        _mx = get_max_active()
        if _mx and count_active_numbers(uid) >= _mx:
            try:
                await query.answer(f"⚠️ Limit: {_mx} active numbers. Release one first (🗑️ RELEASE).", show_alert=True)
            except Exception:
                pass
            return

        # MULTI-NODE BURST: ask for burst quantity instead of instant allocation
        if context.user_data.get("multi_mode"):
            sid = context.user_data.get("la_sid", "")
            context.user_data["multi_range"] = range_text
            qty_txt = (
                f"{head('BURST CONFIG', '🎰')}"
                f"🎯 <b>Service</b> ▸ <b>{html.escape(str(sid))}</b>\n"
                f"🛰️ <b>Range</b>   ▸ <code>{html.escape(str(range_text))}</code>\n\n"
                f"⚡ <b>কতগুলো নোড বার্স্ট করবেন? / Select burst quantity:</b>"
            )
            await query.message.edit_text(
                qty_txt,
                parse_mode="HTML",
                reply_markup=_build_qty_keyboard(uid)
            )
            return

        sid = context.user_data.get("la_sid", "")
        # Admin-tunable: N numbers per pick from GET NUMBER flow (unlimited)
        npp = get_numbers_per_pick()
        if npp >= 2:
            try:
                await query.message.edit_text(
                    f"{head('MULTI SHOT', '⚡')}Injecting <code>{npp}</code> nodes from <code>{html.escape(str(range_text))}</code>...",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            await request_queue.put({
                'type': 'process_numbers',
                'update': update,
                'context': context,
                'range_text': range_text,
                'count': npp
            })
            return
        asyncio.create_task(fast_allocate_number(query, context, range_text, sid))
        return

    # MULTI-NODE BURST — QUANTITY SELECTION
    if data.startswith("mqty_"):
        lang = get_user_lang(uid)
        if not context.user_data.get("multi_mode"):
            try:
                await query.answer("Session expired. Re-open MULTI panel.", show_alert=True)
            except Exception:
                pass
            return
        try:
            count = int(data.replace("mqty_", ""))
        except Exception:
            count = 2
        count = max(2, min(count, MULTI_MAX))
        range_text = context.user_data.get("multi_range")
        if not range_text:
            try:
                await query.answer("Range missing. Re-try.", show_alert=True)
            except Exception:
                pass
            return
        context.user_data["multi_mode"] = False
        context.user_data["multi_range"] = None
        try:
            await query.message.edit_text(
                f"{head('BURST ENGAGED', '🎰')}⚡ Injecting <code>{count}</code> nodes from <code>{html.escape(str(range_text))}</code>...",
                parse_mode="HTML"
            )
        except Exception:
            pass
        await request_queue.put({
            'type': 'process_numbers',
            'update': update,
            'context': context,
            'range_text': range_text,
            'count': count
        })
        return

    # CUSTOM RANGE SETTINGS
    if data == "custom_range":
        context.user_data["mode"] = "custom_range"
        await query.message.edit_text(
            LANG_TEXTS[get_user_lang(uid)]["custom_range_prompt"],
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                make_inline_keyboard_button(BTN[get_user_lang(uid)]["back"], callback_data="back_services", style="danger" if get_user_lang(uid) == "en" else None)
            ]])
        )
        return

    # BACK TO SERVICES CALLBACK
    if data == "back_services":
        services = filter_visible_services(get_cached_services()) or filter_visible_services(context.user_data.get("la_services", []))
        if not services:
            await query.message.edit_text(f"🚧 『 {ub('MAINTENANCE')} 』\n\nNo services available right now.")
            return
        context.user_data["la_services"] = services
        context.user_data["mode"] = None
        multi = bool(context.user_data.get("multi_mode"))
        keyboard = _build_services_keyboard(services, uid, multi=multi)
        await query.message.edit_text(
            node_market_header(get_user_lang(uid), multi=multi),
            parse_mode="HTML",
            reply_markup=keyboard
        )
        return

    # SAME RANGE POLLING
    if data == "same_range":
        r_text = last_range.get(uid)
        if r_text:
            try:
                s_ok, s_pri, s_dgr = _styles(get_user_lang(uid))
                await query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup([[
                    make_inline_keyboard_button(BTN[get_user_lang(uid)]["otp_feed"], url="https://t.me/numbergenfastbot", style=s_pri)
                ]]))
            except Exception:
                pass
            await process_numbers(update, context, r_text, get_numbers_per_pick())
        return

    # NEW NUMBER screen actions (ms_fast-style GET NUMBER result)
    if data == "ms_again":
        rng = last_range.get(uid)
        sid = context.user_data.get("la_sid", "") or (quick_pick_get(uid) or {}).get("sid", "")
        if not rng:
            try:
                await query.answer("Use GET NUMBER first.", show_alert=True)
            except Exception:
                pass
            return
        mx = get_max_active()
        if mx and count_active_numbers(uid) >= mx:
            try:
                await query.answer(f"⚠️ Limit: {mx} active numbers. Release one first.", show_alert=True)
            except Exception:
                pass
            return
        asyncio.create_task(fast_allocate_number(query, context, rng, sid))
        return
    if data == "ms_change":
        rng = last_range.get(uid)
        sid = context.user_data.get("la_sid", "") or (quick_pick_get(uid) or {}).get("sid", "")
        if not rng:
            try:
                await query.answer("Use GET NUMBER first.", show_alert=True)
            except Exception:
                pass
            return
        # v16.13: Change Number = ALL OLD nodes removed, only NEW number(s) remain.
        # Release every active number of this user first — the old number never stays.
        for cur in user_active_numbers(uid, 999):
            release_number(cur, uid)
        # prefer a different range of the same country when alternatives exist
        ranges = context.user_data.get("la_ranges", []) or []
        alt = None
        for g in (context.user_data.get("la_countries", []) or []):
            for i in (g.get("idxs") or []):
                if i < len(ranges) and _norm_rid(str(ranges[i])) != _norm_rid(str(rng)):
                    alt = str(ranges[i])
                    break
            if alt:
                break
        # fresh allocation exactly like GET NUMBER (per-pick count) — everything is NEW
        asyncio.create_task(process_numbers(update, context, alt or rng, get_numbers_per_pick()))
        return

    # WITHDRAW TRIGGERS
    if data == "withdraw_start":
        balance = get_user(uid)['balance']
        if balance < MIN_WITHDRAW:
            await query.message.reply_text(
                LANG_TEXTS[get_user_lang(uid)]["withdraw_min_err"].format(bal=format_balance(balance), min_val=MIN_WITHDRAW),
                parse_mode="HTML"
            )
            return
        context.user_data["withdraw_mode"] = "select_method"
        await query.message.reply_text(
            LANG_TEXTS[get_user_lang(uid)]["withdraw_method_prompt"],
            parse_mode="HTML",
            reply_markup=withdraw_method_keyboard(uid)
        )
        return

    if data == "withdraw_confirm":
        await process_withdraw_confirm(update, context)
        return

    if data == "withdraw_cancel":
        await process_withdraw_cancel(update, context)
        return

    if data.startswith("admin_approve_"):
        await admin_approve_withdraw(update, context, data.replace("admin_approve_", ""))
        return

    if data.startswith("admin_reject_"):
        await admin_reject_withdraw(update, context, data.replace("admin_reject_", ""))
        return

    if data.startswith("my_ref_"):
        target_uid = data.replace("my_ref_", "")
        if str(target_uid) != str(uid) and not is_admin(uid):
            return
        all_logs = load_data(ACTIVITY_LOGS_FILE)
        if not isinstance(all_logs, list):
            all_logs = []
        my_referrals = [log for log in all_logs if str(log.get('uid')) == str(target_uid) and log.get('action') == "REFERRAL_JOINED"]
        content = f"👥 REPLICATION METRICS — {target_uid}\n━━━━━━━━━━━━━━━━━━━━━━\nTOTAL NODES: {len(my_referrals)}\n\n"
        for i, log in enumerate(my_referrals, 1):
            try:
                dt_obj = datetime.fromisoformat(log['timestamp'])
                ref_id = log.get('details', {}).get('referred_user', 'N/A')
                content += f"{i}. NODE_ID: {ref_id} | {dt_obj.strftime('%d/%m/%Y %I:%M %p')}\n"
            except Exception:
                continue
        f = io.BytesIO(content.encode())
        f.name = f"REF_{target_uid}.txt"
        await context.bot.send_document(chat_id=uid, document=f, caption=f"✅ <b>{ub('REPLICATION LOGS')}</b>", parse_mode="HTML")
        return

    if data.startswith("full_logs_"):
        if not is_admin(uid):
            return
        target_uid = data.replace("full_logs_", "")
        stats = get_user_stats(target_uid)
        all_logs = load_data(ACTIVITY_LOGS_FILE)
        if not isinstance(all_logs, list):
            all_logs = []
        user_db = load_data(USER_DATA_FILE)
        user_info = user_db.get(str(target_uid), {})
        user_otps = [log for log in all_logs if str(log.get('uid')) == str(target_uid) and log.get('action') == "OTP_RECEIVED"]
        content = (
            f"📊 PACKET ANALYSIS — {target_uid}\n"
            f"💰 WALLET: {user_info.get('balance', 0):.2f} BDT\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"TODAY NODES: {stats['today_numbers']}\n"
            f"TODAY OTPS: {stats['today_otps']}\n"
            f"ALL NODES: {stats['total_numbers']}\n"
            f"ALL OTPS: {stats['total_otps']}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\nDECRYPTED PACKET LOGS:\n"
        )
        for i, log in enumerate(user_otps, 1):
            try:
                dt_obj = datetime.fromisoformat(log['timestamp'])
                d = log.get('details', {})
                content += f"{i}. {dt_obj.strftime('%d/%m/%Y %I:%M %p')}\n   📟 ADDR: {d.get('number', 'N/A')}\n   🔑 KEY: {d.get('otp', 'N/A')}\n\n"
            except Exception:
                continue
        f = io.BytesIO(content.encode())
        f.name = f"USER_{target_uid}.txt"
        await context.bot.send_document(
            chat_id=uid, document=f,
            caption=f"✅ <b>PACKET DATA DUMP ▸ <code>{target_uid}</code></b>",
            parse_mode="HTML"
        )
        return

# ==================== MAIN CORE MODULES ====================

async def post_init(application):
    global request_queue
    request_queue = asyncio.Queue()
    restore_active_numbers()
    # Premium emoji: load map & prune ids Telegram doesn't know
    try:
        ok, bad = await pe_validate_ids(application.bot)
        print(f"🎨 premium emoji: {ok} valid id(s), {bad} pruned, engine {'ON' if pe_active() else 'OFF'}")
    except Exception as e:
        print(f"premium emoji validate fail: {e}")
    for _ in range(20):
        asyncio.create_task(worker())
    asyncio.create_task(monitor_loop(application))
    asyncio.create_task(liveaccess_refresh_loop())
    asyncio.create_task(auto_discovery_loop(application))
    asyncio.create_task(live_feed_loop(application))
    asyncio.create_task(panel_watchdog_loop(application))
    asyncio.create_task(auto_backup_loop(application))
    print(f"🧩 PANELS: {', '.join(p['name'] for p in active_panels())}  |  💎 WORLD-CLASS PACK v16.16 ZERO-DEP (single file, NO pip needed — stdlib shims for telegram/httpx/pyotp + dual panel + ⚡ SIMPLE premium animation + 🔴 live OTP feed ONE-BY-ONE + 📍 ms_fast GET NUMBER UI + 🟦 custom square menu + 🛠 custom range in GET NUMBER + 🔥 traffic ranges w/ service + ⭐ service+OTP cards + 🗂️ STRICT range/OTP split + 💰 per-panel ৳/OTP rates + 🔁 change=ALL-NEW nodes + 🛡️ admin panel (admins) + 🩺 panel retry/diagnostics + 🛡️ outage-proof service cache) loaded")
    print("🤖 AUTO DISCOVERY: services / countries / ranges are learned live from the network")
    # Premium command menu inside Telegram UI
    try:
        await application.bot.set_my_commands([
            BotCommand("start", "🏠 Main menu"),
            BotCommand("help", "❓ All commands"),
        ])
    except Exception as e:
        print(f"set_my_commands fail: {e}")
    # "What can this bot do?" text (shown before /start) — reference wording
    try:
        await application.bot.set_my_description(
            "Unlimited free numbers — 24/7 SMS & OTP rental bot. Instant delivery, per-country rates, live stock status."
        )
        await application.bot.set_my_short_description("24/7 SMS & OTP numbers • instant delivery • live stock")
    except Exception as e:
        print(f"set_my_description fail: {e}")

def main():
    # PremiumBot = ExtBot + automatic premium (custom) emoji on every message & button
    app = ApplicationBuilder().bot(PremiumBot(token=BOT_TOKEN)).concurrent_updates(True).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("get1number", get1number_command))
    app.add_handler(CommandHandler("multi", multi_command))
    app.add_handler(CommandHandler("searchotp", searchotp_command))
    app.add_handler(CommandHandler("balance", balance_command))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("traffic", traffic_command))
    app.add_handler(CommandHandler("stock", stock_command))
    app.add_handler(CommandHandler("support", support_command))
    app.add_handler(CommandHandler("refer", refer_command))
    app.add_handler(CommandHandler("leaderboard", leaderboard_command))
    app.add_handler(CommandHandler("get2fa", get_2fa_code))
    app.add_handler(CommandHandler("range", range_command))
    app.add_handler(CommandHandler("cloneemoji", cloneemoji_command))
    app.add_handler(CommandHandler("panels", panels_command))
    app.add_handler(CommandHandler("setrangegroup", setrangegroup_command))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, group_id_logger))
    app.add_handler(CommandHandler("anim", anim_command))
    app.add_handler(CommandHandler("livefeed", livefeed_command))
    app.add_handler(CommandHandler("feedphoto", feedphoto_command))
    app.add_handler(CommandHandler("autodisc", autodisc_command))
    app.add_handler(CommandHandler("setpick", setpick_command))
    app.add_handler(CommandHandler("addpanel", addpanel_command))
    app.add_handler(CommandHandler("delpanel", delpanel_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("daily", daily_command))
    app.add_handler(CommandHandler("setdaily", setdaily_command))
    app.add_handler(CommandHandler("setmaxactive", setmaxactive_command))
    app.add_handler(CommandHandler("addrange", addrange_command))
    app.add_handler(CommandHandler("delrange", delrange_command))

    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.AUDIO
         | filters.VOICE | filters.ANIMATION | filters.Sticker.ALL) & (~filters.COMMAND),
        handle_message
    ))

    print("🚀 BOT RUNNING...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()

