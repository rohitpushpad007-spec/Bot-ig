print("STEP 1: python started", flush=True)

import asyncio
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

print("STEP 2: stdlib imports done", flush=True)

from telegram import Update, ReactionTypeEmoji
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

print("STEP 3: python-telegram-bot imported", flush=True)

import config

print("STEP 4: config imported", flush=True)

import ig_client

print("STEP 5: ig_client (instagrapi) imported", flush=True)

import other_downloads
