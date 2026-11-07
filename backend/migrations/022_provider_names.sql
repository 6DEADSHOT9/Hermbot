DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='providers' AND column_name='name_is_custom') THEN
        ALTER TABLE providers ADD COLUMN name_is_custom boolean NOT NULL DEFAULT true;
        UPDATE providers SET name_is_custom=false
        WHERE (auth_kind='codex_oauth' AND name='Codex ' || left(replace(id::text,'-',''),8))
           OR (auth_kind='chatgpt_oauth' AND name='ChatGPT ' || left(replace(id::text,'-',''),8));
    END IF;
END $$;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS account_plan text NOT NULL DEFAULT '';
