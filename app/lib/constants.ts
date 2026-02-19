export const MAX_FILES         = 5;
export const MAX_FILE_MB       = 10;
export const MAX_QUERY_CHARS   = 2000;
export const DAILY_MSG_LIMIT   = 20;
export const ALLOWED_EXTENSIONS = ["pdf", "docx", "txt", "csv", "md", "png", "jpg", "jpeg", "tiff"];
export const ALLOWED_ACCEPT    = ALLOWED_EXTENSIONS.map((e) => `.${e}`).join(",");
