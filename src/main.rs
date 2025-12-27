use std::env;
use std::sync::Arc;
use std::time::Duration;

use anitomy::{Anitomy, ElementCategory};
use axum::Router;
use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::middleware::{Next, from_fn, from_fn_with_state};
use axum::response::{IntoResponse, Redirect, Response};
use axum::routing::get;
use moka::future::Cache;
use rand::prelude::IndexedRandom;
use regex::Regex;
use reqwest::redirect::Policy;
use scraper::{Html, Selector};
use serde::Deserialize;
use serde_json::Value;
use tokio::sync::RwLock;
use tracing_subscriber::EnvFilter;
use url::form_urlencoded;
use utoipa::{OpenApi, ToSchema};
use utoipa_swagger_ui::SwaggerUi;

mod tvdb;
use tvdb::TVDB;

#[derive(Clone)]
struct AppState {
    tvdb: Arc<RwLock<TVDB>>,
    http: reqwest::Client,
    poster_cache: Cache<String, String>,
    fanart_cache: Cache<String, String>,
    torrent_cache: Cache<String, String>,
}

#[derive(Debug, Clone)]
struct ParsedQuery {
    file_name: String,
    anime_title: String,
    anime_year: Option<String>,
    anime_season: Option<String>,
}

#[derive(Deserialize, ToSchema)]
struct QueryParam {
    query: String,
}

#[derive(Deserialize, ToSchema)]
struct TorrentQuery {
    url: String,
}

#[derive(OpenApi)]
#[openapi(
    info(
        title = "mona",
        version = "0.1.0",
        license(
            name = "MIT",
            identifier = "MIT"
        )
    ),
    paths(poster, fanart, torrent_art, healthcheck),
    components(schemas(QueryParam, TorrentQuery)),
    tags(
        (name = "mona", description = "Art redirect endpoints")
    )
)]
struct ApiDoc;

#[tokio::main]
async fn main() {
    dotenvy::dotenv().ok();
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    tracing_subscriber::fmt()
        .compact()
        .with_target(false)
        .with_env_filter(filter)
        .init();

    let api_key = match env::var("TVDB_API_KEY") {
        Ok(value) if !value.trim().is_empty() => value,
        _ => {
            eprintln!("TVDB_API_KEY is required but was not set.");
            std::process::exit(1);
        }
    };
    let ttl = Duration::from_secs(60 * 60 * 24 * 3);
    let state = AppState {
        tvdb: Arc::new(RwLock::new(TVDB::new(api_key))),
        http: reqwest::Client::builder()
            .redirect(Policy::limited(10))
            .build()
            .unwrap(),
        poster_cache: Cache::builder()
            .max_capacity(5000)
            .time_to_live(ttl)
            .build(),
        fanart_cache: Cache::builder()
            .max_capacity(5000)
            .time_to_live(ttl)
            .build(),
        torrent_cache: Cache::builder()
            .max_capacity(5000)
            .build(),
    };

    let poster_router = Router::new()
        .route("/poster", get(poster))
        .route_layer(from_fn_with_state(state.clone(), cache_poster_middleware))
        .route_layer(from_fn_with_state(state.clone(), tvdb_login_middleware))
        .with_state(state.clone());

    let fanart_router = Router::new()
        .route("/fanart", get(fanart))
        .route_layer(from_fn_with_state(state.clone(), cache_fanart_middleware))
        .route_layer(from_fn_with_state(state.clone(), tvdb_login_middleware))
        .with_state(state.clone());

    let torrent_router = Router::new()
        .route("/torrent-art", get(torrent_art))
        .route_layer(from_fn_with_state(state.clone(), cache_torrent_middleware))
        .with_state(state.clone());

    let app = Router::new()
        .merge(SwaggerUi::new("/").url("/api-doc/openapi.json", ApiDoc::openapi()))
        .route("/healthcheck", get(healthcheck).head(healthcheck))
        .merge(poster_router)
        .merge(fanart_router)
        .merge(torrent_router)
        .layer(from_fn(request_logging_middleware));

    let addr = "0.0.0.0:3000";
    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    println!("listening on {}", listener.local_addr().unwrap());
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .unwrap();
}

async fn shutdown_signal() {
    let ctrl_c = tokio::signal::ctrl_c();

    #[cfg(unix)]
    let terminate = async {
        let mut sigterm = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("install SIGTERM handler");
        sigterm.recv().await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }
}

#[utoipa::path(
    get,
    path = "/poster",
    params(
        ("query" = String, Query, description = "Filename or search string")
    ),
    responses(
        (status = 307, description = "Redirect to poster", headers(
            ("Location" = String, description = "Poster URL")
        )),
        (status = 400, description = "Invalid query"),
        (status = 404, description = "Poster not found"),
        (status = 502, description = "Upstream error")
    ),
    tag = "mona"
)]
async fn poster(
    State(state): State<AppState>,
    Query(params): Query<QueryParam>,
) -> Result<Redirect, (StatusCode, String)> {
    let parsed = parse_query(&params.query)
        .ok_or((StatusCode::BAD_REQUEST, "query is invalid".to_string()))?;

    let tvdb = state.tvdb.read().await;
    if let Some(poster) = get_tvdb_poster(&tvdb, &parsed)
        .await
        .map_err(|_| (StatusCode::BAD_GATEWAY, "tvdb request failed".to_string()))?
    {
        return Ok(Redirect::temporary(&poster));
    }

    if let Some(poster) = get_subsplease_poster(&state.http, &parsed.anime_title)
        .await
        .map_err(|_| {
            (
                StatusCode::BAD_GATEWAY,
                "subsplease request failed".to_string(),
            )
        })?
    {
        return Ok(Redirect::temporary(&poster));
    }

    Err((StatusCode::NOT_FOUND, "poster not found".to_string()))
}

#[utoipa::path(
    get,
    path = "/fanart",
    params(
        ("query" = String, Query, description = "Filename or search string")
    ),
    responses(
        (status = 307, description = "Redirect to fanart", headers(
            ("Location" = String, description = "Fanart URL")
        )),
        (status = 400, description = "Invalid query"),
        (status = 404, description = "Fanart not found"),
        (status = 502, description = "Upstream error")
    ),
    tag = "mona"
)]
async fn fanart(
    State(state): State<AppState>,
    Query(params): Query<QueryParam>,
) -> Result<Redirect, (StatusCode, String)> {
    let parsed = parse_query(&params.query)
        .ok_or((StatusCode::BAD_REQUEST, "query is invalid".to_string()))?;

    let tvdb = state.tvdb.read().await;
    let fanart = get_fanart(&tvdb, &parsed)
        .await
        .map_err(|_| (StatusCode::BAD_GATEWAY, "tvdb request failed".to_string()))?;
    let Some(fanart) = fanart else {
        return Err((StatusCode::NOT_FOUND, "fanart not found".to_string()));
    };

    let image = fanart
        .choose(&mut rand::rng())
        .and_then(|value| value.get("image"))
        .and_then(|value| value.as_str())
        .ok_or((StatusCode::NOT_FOUND, "fanart not found".to_string()))?;

    Ok(Redirect::temporary(image))
}

#[utoipa::path(
    get,
    path = "/torrent-art",
    params(
        ("url" = String, Query, description = "Torrent page URL")
    ),
    responses(
        (status = 307, description = "Redirect to art", headers(
            ("Location" = String, description = "Art URL")
        )),
        (status = 400, description = "Invalid URL"),
        (status = 404, description = "Art not found"),
        (status = 502, description = "Upstream error")
    ),
    tag = "mona"
)]
async fn torrent_art(
    State(state): State<AppState>,
    Query(params): Query<TorrentQuery>,
) -> Result<Redirect, (StatusCode, String)> {
    if !params.url.starts_with("https://nyaa.si")
        && !params.url.starts_with("https://sukebei.nyaa.si/")
    {
        return Err((StatusCode::BAD_REQUEST, "invalid url".to_string()));
    }

    let image = get_torrent_art(&state.http, &params.url)
        .await
        .map_err(|_| {
            (
                StatusCode::BAD_GATEWAY,
                "torrent request failed".to_string(),
            )
        })?
        .ok_or((StatusCode::NOT_FOUND, "art not found".to_string()))?;

    Ok(Redirect::temporary(&image))
}

#[utoipa::path(
    get,
    path = "/healthcheck",
    responses(
        (status = 200, description = "OK")
    ),
    tag = "mona"
)]
async fn healthcheck() -> StatusCode {
    StatusCode::OK
}

async fn tvdb_login_middleware(
    State(state): State<AppState>,
    req: axum::http::Request<axum::body::Body>,
    next: Next,
) -> Response {
    if ensure_tvdb_login(&state.tvdb).await.is_err() {
        return (StatusCode::BAD_GATEWAY, "tvdb login failed").into_response();
    }
    next.run(req).await
}

async fn request_logging_middleware(
    req: axum::http::Request<axum::body::Body>,
    next: Next,
) -> Response {
    let method = req.method().clone();
    let uri = req.uri().clone();
    let path = uri.path();
    if path == "/healthcheck" {
        return next.run(req).await;
    }

    let start = std::time::Instant::now();
    let response = next.run(req).await;
    tracing::info!(
        method = %method,
        uri = %uri,
        status = response.status().as_u16(),
        latency_ms = start.elapsed().as_millis(),
        "request"
    );
    response
}

async fn cache_poster_middleware(
    State(state): State<AppState>,
    req: axum::http::Request<axum::body::Body>,
    next: Next,
) -> Response {
    cache_redirect_by_query(state.poster_cache.clone(), "query", req, next).await
}

async fn cache_fanart_middleware(
    State(state): State<AppState>,
    req: axum::http::Request<axum::body::Body>,
    next: Next,
) -> Response {
    cache_redirect_by_query(state.fanart_cache.clone(), "query", req, next).await
}

async fn cache_torrent_middleware(
    State(state): State<AppState>,
    req: axum::http::Request<axum::body::Body>,
    next: Next,
) -> Response {
    cache_redirect_by_query(state.torrent_cache.clone(), "url", req, next).await
}

async fn cache_redirect_by_query(
    cache: Cache<String, String>,
    param_name: &str,
    req: axum::http::Request<axum::body::Body>,
    next: Next,
) -> Response {
    let cache_key = extract_query_param(req.uri(), param_name);
    if let Some(key) = cache_key.as_ref()
        && let Some(url) = cache.get(key).await
    {
        return Redirect::temporary(&url).into_response();
    }

    let response = next.run(req).await;
    if response.status() == StatusCode::TEMPORARY_REDIRECT
        && let (Some(key), Some(location)) = (
            cache_key,
            response
                .headers()
                .get(axum::http::header::LOCATION)
                .and_then(|value| value.to_str().ok())
                .map(|value| value.to_string()),
        )
    {
        cache.insert(key, location).await;
    }

    response
}

fn extract_query_param(uri: &axum::http::Uri, key: &str) -> Option<String> {
    let query = uri.query()?;
    for (k, v) in form_urlencoded::parse(query.as_bytes()) {
        if k == key {
            return Some(v.into_owned());
        }
    }
    None
}

fn parse_query(query: &str) -> Option<ParsedQuery> {
    let mut parser = Anitomy::new();
    let elements = parser.parse(query).ok()?;

    let anime_title = elements.get(ElementCategory::AnimeTitle)?.to_string();
    let anime_year = elements.get(ElementCategory::AnimeYear).map(str::to_string);
    let anime_season = elements
        .get(ElementCategory::AnimeSeason)
        .map(str::to_string);

    Some(ParsedQuery {
        file_name: query.to_string(),
        anime_title,
        anime_year,
        anime_season,
    })
}

fn get_search_string(parsed: &ParsedQuery) -> String {
    match &parsed.anime_year {
        Some(year) => format!("{} ({})", parsed.anime_title, year),
        None => parsed.anime_title.clone(),
    }
}

async fn ensure_tvdb_login(tvdb: &Arc<RwLock<TVDB>>) -> Result<(), reqwest::Error> {
    {
        let reader = tvdb.read().await;
        if !reader.needs_login() {
            return Ok(());
        }
    }

    let mut writer = tvdb.write().await;
    if writer.needs_login() {
        writer.login().await?;
    }
    Ok(())
}

async fn get_tvdb_poster(
    tvdb: &TVDB,
    parsed: &ParsedQuery,
) -> Result<Option<String>, reqwest::Error> {
    let mut series = find_best_match(tvdb, parsed).await?;
    if series.is_none() {
        return Ok(None);
    }

    let series = series.take().unwrap();
    let mut series_id = extract_tvdb_id(&series);
    let mut series_image = extract_image_url(&series).map(str::to_string);
    if series_id.is_none()
        && series_image.is_none()
        && let Some(fallback) = find_best_match_by_title(tvdb, parsed).await?
    {
        series_id = extract_tvdb_id(&fallback);
        series_image = extract_image_url(&fallback).map(str::to_string);
    }
    if series_image.is_none()
        && let Some(series_id) = series_id
        && let Some(details) = tvdb.get_series_extended(series_id).await?
    {
        series_image = extract_image_url(&details).map(str::to_string);
    }
    let season = parsed.anime_season.as_deref();

    if series_id.is_none() || season.is_none() {
        return Ok(series_image);
    }

    let season_image = get_season_image(tvdb, series_id.unwrap(), season.unwrap()).await?;
    Ok(season_image.or(series_image))
}

fn extract_tvdb_id(value: &Value) -> Option<i64> {
    let keys = ["tvdb_id", "id", "objectID", "objectId", "object_id"];
    for key in keys {
        if let Some(raw) = value.get(key) {
            if let Some(id) = raw.as_i64() {
                return Some(id);
            }
            if let Some(id) = raw.as_str().and_then(|s| s.parse::<i64>().ok()) {
                return Some(id);
            }
        }
    }
    None
}

fn extract_image_url(value: &Value) -> Option<&str> {
    value
        .get("image_url")
        .and_then(|value| value.as_str())
        .or_else(|| value.get("image").and_then(|value| value.as_str()))
}

async fn get_season_image(
    tvdb: &TVDB,
    tvdb_id: i64,
    season_number: &str,
) -> Result<Option<String>, reqwest::Error> {
    let series = tvdb.get_series_extended(tvdb_id).await?;
    let Some(series) = series else {
        return Ok(None);
    };

    let seasons = series
        .get("seasons")
        .and_then(|value| value.as_array())
        .cloned()
        .unwrap_or_default();

    let season_number_int = season_number.parse::<i64>().ok();
    let season_id = seasons.iter().find_map(|season| {
        let number = season.get("number").and_then(|value| value.as_i64());
        if number == season_number_int {
            season.get("id").and_then(|value| value.as_i64())
        } else {
            None
        }
    });

    let Some(season_id) = season_id else {
        return Ok(None);
    };

    let season_details = tvdb.get_season_extended(season_id).await?;
    let Some(season_details) = season_details else {
        return Ok(None);
    };

    let artwork = season_details
        .get("artwork")
        .and_then(|value| value.as_array())
        .cloned()
        .unwrap_or_default();

    let image = artwork.iter().find_map(|item| {
        let art_type = item.get("type").and_then(|value| value.as_i64());
        if art_type == Some(7) {
            item.get("image").and_then(|value| value.as_str())
        } else {
            None
        }
    });

    Ok(image.map(str::to_string))
}

async fn find_best_match(
    tvdb: &TVDB,
    parsed: &ParsedQuery,
) -> Result<Option<Value>, reqwest::Error> {
    let (results, query) = search_candidates(tvdb, parsed).await?;
    if results.is_empty() {
        return Ok(None);
    }
    let selected = select_best_match(&results, &query);
    Ok(selected.cloned())
}

async fn find_best_match_by_title(
    tvdb: &TVDB,
    parsed: &ParsedQuery,
) -> Result<Option<Value>, reqwest::Error> {
    let (results, query) = search_by_title(tvdb, parsed).await?;
    if results.is_empty() {
        return Ok(None);
    }
    let selected = select_best_match(&results, &query);
    Ok(selected.cloned())
}

async fn search_candidates(
    tvdb: &TVDB,
    parsed: &ParsedQuery,
) -> Result<(Vec<Value>, String), reqwest::Error> {
    if !parsed.file_name.is_empty() {
        let results = tvdb.search(&parsed.file_name).await?;
        if !results.is_empty() {
            return Ok((results, parsed.file_name.clone()));
        }
    }

    search_by_title(tvdb, parsed).await
}

async fn search_by_title(
    tvdb: &TVDB,
    parsed: &ParsedQuery,
) -> Result<(Vec<Value>, String), reqwest::Error> {
    let mut search_string = get_search_string(parsed);
    let mut results = tvdb.search(&search_string).await?;
    if results.is_empty() {
        search_string = parsed.anime_title.clone();
        results = tvdb.search(&search_string).await?;
    }
    Ok((results, search_string))
}

fn select_best_match<'a>(results: &'a [Value], query: &str) -> Option<&'a Value> {
    results.iter().max_by(|a, b| {
        let score_a = hybrid_priority_score(a, query);
        let score_b = hybrid_priority_score(b, query);
        score_a.total_cmp(&score_b)
    })
}

fn hybrid_priority_score(obj: &Value, query: &str) -> f32 {
    let image_url = extract_image_url(obj);
    let has_image = if image_url.is_some() && !image_url.unwrap_or("").contains("missing") {
        20.0
    } else {
        0.0
    };

    let primary_language = obj
        .get("primary_language")
        .and_then(|value| value.as_str())
        .unwrap_or("");
    let is_asian = if matches!(primary_language, "jpn" | "kor" | "zho") {
        30.0
    } else {
        0.0
    };

    let is_series = if obj.get("type").and_then(|value| value.as_str()) == Some("series") {
        10.0
    } else {
        0.0
    };

    let data = obj.to_string().to_lowercase();
    let is_anime = if data.contains("anime") || data.contains("crunchyroll") {
        20.0
    } else {
        0.0
    };

    let title_relevance = calculate_enhanced_title_relevance(obj, query);
    let anime_score = has_image + is_asian + is_series + is_anime;

    let weighted_title_score = title_relevance * 0.6;
    let weighted_anime_score = anime_score * 0.4;

    weighted_title_score + weighted_anime_score
}

fn calculate_enhanced_title_relevance(obj: &Value, query: &str) -> f32 {
    if query.trim().is_empty() {
        return 0.0;
    }

    let query_lower = query.to_lowercase();

    let eng_translation = obj
        .get("translations")
        .and_then(|value| value.get("eng"))
        .and_then(|value| value.as_str())
        .unwrap_or("")
        .to_lowercase();
    let name = obj
        .get("name")
        .and_then(|value| value.as_str())
        .unwrap_or("")
        .to_lowercase();
    let slug = obj
        .get("slug")
        .and_then(|value| value.as_str())
        .unwrap_or("")
        .to_lowercase();
    let slug = slug.replace('-', " ");

    let aliases: Vec<String> = obj
        .get("aliases")
        .and_then(|value| value.as_array())
        .map(|aliases| {
            aliases
                .iter()
                .filter_map(|alias| alias.as_str())
                .map(|alias| alias.to_lowercase())
                .collect()
        })
        .unwrap_or_default();

    if eng_translation == query_lower || name == query_lower || aliases.contains(&query_lower) {
        return 100.0;
    }

    let slug_without_year = Regex::new(r"-\d+$").unwrap();
    let slug_trimmed = slug_without_year.replace(&slug, "");
    if slug_trimmed == query_lower.replace(' ', "-") {
        return 100.0;
    }

    let query_slug = query_lower.replace(' ', "-");
    if eng_translation.contains(&query_lower)
        || name.contains(&query_lower)
        || slug.contains(&query_slug)
        || aliases.iter().any(|alias| alias.contains(&query_lower))
    {
        return 90.0;
    }

    let query_words: Vec<&str> = query_lower
        .split_whitespace()
        .filter(|w| w.len() > 1)
        .collect();
    if query_words.is_empty() {
        return 0.0;
    }

    let mut all_text = vec![eng_translation, name, slug];
    all_text.extend(aliases);
    let all_text = all_text.join(" ");

    let matched_words = query_words
        .iter()
        .filter(|word| all_text.contains(**word))
        .count();
    (matched_words as f32 / query_words.len() as f32) * 80.0
}

async fn get_fanart(
    tvdb: &TVDB,
    parsed: &ParsedQuery,
) -> Result<Option<Vec<Value>>, reqwest::Error> {
    let (results, query) = search_candidates(tvdb, parsed).await?;
    if results.is_empty() {
        return Ok(None);
    }

    let mut candidates: Vec<&Value> = results.iter().collect();
    candidates.sort_by(|a, b| {
        let score_a = hybrid_priority_score(a, &query);
        let score_b = hybrid_priority_score(b, &query);
        score_b.total_cmp(&score_a)
    });

    for candidate in candidates {
        if let Some(fanart) = fanart_for_candidate(tvdb, candidate).await? {
            return Ok(Some(fanart));
        }
    }

    Ok(None)
}

async fn fanart_for_candidate(
    tvdb: &TVDB,
    series: &Value,
) -> Result<Option<Vec<Value>>, reqwest::Error> {
    let series_id = extract_tvdb_id(series);
    let series_type = series.get("type").and_then(|value| value.as_str());

    let Some(series_id) = series_id else {
        return Ok(None);
    };

    match series_type {
        Some("series") => {
            let artworks = tvdb.get_series_artworks(series_id, None, Some(3)).await?;
            let artworks = artworks
                .and_then(|value| value.get("artworks").cloned())
                .and_then(|value| value.as_array().cloned());
            Ok(artworks)
        }
        Some("movie") => {
            let movie = tvdb.get_movie_extended(series_id).await?;
            let artworks = movie
                .and_then(|value| value.get("artworks").cloned())
                .and_then(|value| value.as_array().cloned());
            let Some(artworks) = artworks else {
                return Ok(None);
            };
            let filtered: Vec<Value> = artworks
                .into_iter()
                .filter(|item| item.get("type").and_then(|value| value.as_i64()) == Some(15))
                .collect();
            if filtered.is_empty() {
                Ok(None)
            } else {
                Ok(Some(filtered))
            }
        }
        _ => Ok(None),
    }
}

async fn get_subsplease_poster(
    client: &reqwest::Client,
    name: &str,
) -> Result<Option<String>, reqwest::Error> {
    let slug = slugify(name);
    let mut words: Vec<&str> = slug.split('-').filter(|w| !w.is_empty()).collect();
    if words.is_empty() {
        words.push("");
    }

    for _ in 0..=words.len() {
        let url = format!("https://subsplease.org/shows/{}", words.join("-"));
        let response = client.get(url).send().await?;
        if response.status().is_success() {
            let body = response.text().await?;
            let document = Html::parse_document(&body);
            let selector = Selector::parse("img").unwrap();
            if let Some(img) = document.select(&selector).next()
                && let Some(src) = img.value().attr("src")
            {
                return Ok(Some(format!("https://subsplease.org{}", src)));
            }
        }
        if !words.is_empty() {
            words.pop();
        }
    }

    Ok(None)
}

async fn get_torrent_art(
    client: &reqwest::Client,
    url: &str,
) -> Result<Option<String>, reqwest::Error> {
    let response = client.get(url).send().await?;
    if !response.status().is_success() {
        return Ok(None);
    }

    let body = response.text().await?;
    let document = Html::parse_document(&body);
    let selector = Selector::parse("div#torrent-description").unwrap();
    let Some(description) = document.select(&selector).next() else {
        return Ok(None);
    };

    let html = description.inner_html();
    let re = Regex::new(r#"https?://[^\s"']+?\.(?:jpg|jpeg|png|gif)"#).unwrap();
    Ok(re.find(&html).map(|value| value.as_str().to_string()))
}

fn slugify(text: &str) -> String {
    let mut text = text.to_lowercase();
    let bracket_re = Regex::new(r"\[.*?\]").unwrap();
    text = bracket_re.replace_all(&text, "").to_string();
    text = text.replace(['(', ')'], "");
    text = text.replace(['\'', '\u{2019}'], "");
    text = text.replace(['+', '@'], "");
    let non_alnum = Regex::new(r"[^a-zA-Z0-9_]+").unwrap();
    text = non_alnum.replace_all(&text, "-").to_string();
    text.trim_matches('-').to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::response::IntoResponse;
    use serde_json::json;

    fn tvdb_api_key() -> Option<String> {
        dotenvy::dotenv().ok();
        std::env::var("TVDB_API_KEY").ok()
    }

    fn test_state(api_key: String) -> AppState {
        let ttl = Duration::from_secs(60 * 60 * 24 * 3);
        AppState {
            tvdb: Arc::new(RwLock::new(TVDB::new(api_key))),
            http: reqwest::Client::builder()
                .redirect(Policy::limited(10))
                .build()
                .unwrap(),
            poster_cache: Cache::builder()
                .max_capacity(5000)
                .time_to_live(ttl)
                .build(),
            fanart_cache: Cache::builder()
                .max_capacity(5000)
                .time_to_live(ttl)
                .build(),
            torrent_cache: Cache::builder()
                .max_capacity(5000)
                .build(),
        }
    }

    async fn ensure_tvdb_ready(state: &AppState) {
        ensure_tvdb_login(&state.tvdb)
            .await
            .expect("tvdb login failed");
    }

    #[test]
    fn test_slugify_basic() {
        let input = "[SubsPlease] My Show (2020) - 01";
        let output = slugify(input);
        assert_eq!(output, "my-show-2020-01");
    }

    #[test]
    fn test_get_search_string() {
        let parsed = ParsedQuery {
            file_name: "file.mkv".to_string(),
            anime_title: "Toradora".to_string(),
            anime_year: Some("2008".to_string()),
            anime_season: None,
        };
        assert_eq!(get_search_string(&parsed), "Toradora (2008)");
    }

    #[test]
    fn test_title_relevance_exact_match() {
        let obj = json!({
            "translations": { "eng": "Yuyushiki" },
            "name": "Yuyushiki",
            "slug": "yuyushiki",
            "aliases": ["Yuyu Shiki"]
        });
        let score = calculate_enhanced_title_relevance(&obj, "Yuyushiki");
        assert_eq!(score, 100.0);
    }

    #[test]
    fn test_title_relevance_partial_match() {
        let obj = json!({
            "translations": { "eng": "Toradora" },
            "name": "Toradora",
            "slug": "toradora",
            "aliases": ["Tiger x Dragon"]
        });
        let score = calculate_enhanced_title_relevance(&obj, "Tiger");
        assert!(score > 0.0);
        assert!(score <= 90.0);
    }

    #[test]
    fn test_hybrid_priority_score_prefers_anime_metadata() {
        let obj = json!({
            "image_url": "https://example.com/poster.jpg",
            "primary_language": "jpn",
            "type": "series",
            "name": "Example",
            "translations": { "eng": "Example" },
            "slug": "example",
            "aliases": []
        });
        let score = hybrid_priority_score(&obj, "Example");
        assert!(score > 0.0);
    }

    #[test]
    fn test_select_best_match() {
        let better = json!({
            "image_url": "https://example.com/poster.jpg",
            "primary_language": "jpn",
            "type": "series",
            "name": "Toradora",
            "translations": { "eng": "Toradora" },
            "slug": "toradora",
            "aliases": []
        });
        let worse = json!({
            "image_url": "missing",
            "primary_language": "eng",
            "type": "movie",
            "name": "Random",
            "translations": { "eng": "Random" },
            "slug": "random",
            "aliases": []
        });
        let results = vec![worse, better.clone()];
        let selected = select_best_match(&results, "Toradora").expect("no match selected");
        assert_eq!(
            selected.get("name").and_then(|v| v.as_str()),
            Some("Toradora")
        );
    }

    #[test]
    fn test_parse_query_from_filename() {
        let query = "[TaigaSubs]_Toradora!_(2008)_-_01v2_-_Tiger_and_Dragon_[1280x720_H.264_FLAC][1234ABCD].mkv";
        let parsed = parse_query(query).expect("failed to parse");
        assert_eq!(parsed.anime_title, "Toradora!");
        assert_eq!(parsed.anime_year.as_deref(), Some("2008"));
    }

    #[tokio::test]
    async fn test_poster_handler() {
        let Some(api_key) = tvdb_api_key() else {
            eprintln!("TVDB_API_KEY not set; skipping poster handler test");
            return;
        };

        let state = test_state(api_key);
        ensure_tvdb_ready(&state).await;
        let query = QueryParam {
            query: "[TaigaSubs]_Toradora!_(2008)_-_01v2_-_Tiger_and_Dragon_[1280x720_H.264_FLAC][1234ABCD].mkv".to_string(),
        };

        let response = poster(State(state), Query(query))
            .await
            .expect("poster handler failed")
            .into_response();

        let location = response
            .headers()
            .get(axum::http::header::LOCATION)
            .and_then(|value| value.to_str().ok())
            .unwrap_or("");

        assert_eq!(response.status(), StatusCode::TEMPORARY_REDIRECT);
        assert!(location.starts_with("http"));
    }

    #[tokio::test]
    async fn test_fanart_handler() {
        let Some(api_key) = tvdb_api_key() else {
            eprintln!("TVDB_API_KEY not set; skipping fanart handler test");
            return;
        };

        let state = test_state(api_key);
        ensure_tvdb_ready(&state).await;
        let query = QueryParam {
            query: "madoka".to_string(),
        };

        let response = fanart(State(state), Query(query))
            .await
            .expect("fanart handler failed")
            .into_response();

        let location = response
            .headers()
            .get(axum::http::header::LOCATION)
            .and_then(|value| value.to_str().ok())
            .unwrap_or("");

        assert_eq!(response.status(), StatusCode::TEMPORARY_REDIRECT);
        assert!(location.starts_with("http"));
    }

    #[tokio::test]
    async fn test_torrent_art_handler() {
        let state = test_state("unused".to_string());
        let query = TorrentQuery {
            url: "https://nyaa.si/view/2055976".to_string(),
        };

        let response = torrent_art(State(state), Query(query))
            .await
            .expect("torrent-art handler failed")
            .into_response();

        let location = response
            .headers()
            .get(axum::http::header::LOCATION)
            .and_then(|value| value.to_str().ok())
            .unwrap_or("");

        assert_eq!(response.status(), StatusCode::TEMPORARY_REDIRECT);
        assert!(location.starts_with("http"));
    }
}
