-- Run this in your Supabase SQL Editor (https://supabase.com/dashboard → SQL Editor)

CREATE TABLE users (
    telegram_id BIGINT PRIMARY KEY,
    splitwise_token TEXT,
    splitwise_user_id BIGINT,
    splitwise_name TEXT,
    setup_complete BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE flatmates (
    id SERIAL PRIMARY KEY,
    user_telegram_id BIGINT REFERENCES users(telegram_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    splitwise_user_id BIGINT NOT NULL,
    UNIQUE(user_telegram_id, splitwise_user_id)
);
