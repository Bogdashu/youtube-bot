#!/bin/bash

telegram-bot-api \
  --local \
  --http-port=8081 \
  --api-id=$TELEGRAM_API_ID \
  --api-hash=$TELEGRAM_API_HASH &

sleep 8

python main.py
