use std::time::{Duration, SystemTime};

use reqwest::Client;
use serde::de::DeserializeOwned;
use serde::Deserialize;
use serde_json::Value;

#[derive(Debug)]
pub struct TVDB {
    api_key: String,
    pin: String,
    api_base: String,
    client: Client,
    token: Option<String>,
    token_expires: Option<SystemTime>,
}

#[derive(Debug, Deserialize)]
struct ApiResponse<T> {
    data: T,
}

impl TVDB {
    pub fn new(api_key: String) -> Self {
        Self {
            api_key,
            pin: "hello world".to_string(),
            api_base: "https://api4.thetvdb.com/v4".to_string(),
            client: Client::new(),
            token: None,
            token_expires: None,
        }
    }

    pub fn needs_login(&self) -> bool {
        match self.token_expires {
            Some(expires) => SystemTime::now() >= expires,
            None => self.token.is_none(),
        }
    }

    pub async fn login(&mut self) -> Result<Option<String>, reqwest::Error> {
        let response = self
            .client
            .post(format!("{}/login", self.api_base))
            .json(&serde_json::json!({
                "apikey": self.api_key,
                "pin": self.pin,
            }))
            .send()
            .await?;

        if response.status().is_success() {
            let data: ApiResponse<Value> = response.json().await?;
            self.token = data
                .data
                .get("token")
                .and_then(|value| value.as_str())
                .map(str::to_string);
            self.token_expires = Some(SystemTime::now() + Duration::from_secs(3600));
        }

        Ok(self.token.clone())
    }

    pub async fn search(&self, query: &str) -> Result<Vec<Value>, reqwest::Error> {
        self.get_list("/search", &[("query", query)]).await
    }

    pub async fn get_series_extended(&self, series_id: i64) -> Result<Option<Value>, reqwest::Error> {
        self.get_one(&format!("/series/{}/extended", series_id), &[])
            .await
    }

    pub async fn get_series_artworks(
        &self,
        series_id: i64,
        lang: Option<&str>,
        art_type: Option<i64>,
    ) -> Result<Option<Value>, reqwest::Error> {
        let mut params: Vec<(&str, &str)> = Vec::new();
        let art_type_value = art_type.map(|value| value.to_string());
        if let Some(lang) = lang {
            params.push(("lang", lang));
        }
        if let Some(ref art_type_value) = art_type_value {
            params.push(("type", art_type_value));
        }
        self.get_one(&format!("/series/{}/artworks", series_id), &params)
            .await
    }

    pub async fn get_movie_extended(&self, movie_id: i64) -> Result<Option<Value>, reqwest::Error> {
        self.get_one(&format!("/movies/{}/extended", movie_id), &[])
            .await
    }

    pub async fn get_season_extended(&self, season_id: i64) -> Result<Option<Value>, reqwest::Error> {
        self.get_one(&format!("/seasons/{}/extended", season_id), &[])
            .await
    }

    async fn get_one(
        &self,
        path: &str,
        params: &[(&str, &str)],
    ) -> Result<Option<Value>, reqwest::Error> {
        let data: Option<Value> = self.get_data(path, params).await?;
        Ok(data)
    }

    async fn get_list(
        &self,
        path: &str,
        params: &[(&str, &str)],
    ) -> Result<Vec<Value>, reqwest::Error> {
        let data: Vec<Value> = self.get_data(path, params).await?;
        Ok(data)
    }

    async fn get_data<T: DeserializeOwned + Default>(
        &self,
        path: &str,
        params: &[(&str, &str)],
    ) -> Result<T, reqwest::Error> {
        let mut request = self
            .client
            .get(format!("{}{}", self.api_base, path))
            .bearer_auth(self.token.as_deref().unwrap_or_default());

        for (key, value) in params {
            request = request.query(&[(*key, *value)]);
        }

        let response = request.send().await?;
        if !response.status().is_success() {
            return Ok(T::default());
        }
        let data: ApiResponse<T> = response.json().await?;
        Ok(data.data)
    }
}

#[cfg(test)]
mod tests {
    use super::TVDB;

    #[tokio::test]
    async fn test_tvdb_api() {
        let api_key = match std::env::var("TVDB_API_KEY") {
            Ok(value) => value,
            Err(_) => {
                eprintln!("TVDB_API_KEY not set; skipping TVDB API test");
                return;
            }
        };

        let mut tvdb = TVDB::new(api_key);
        let token = tvdb.login().await.expect("login request failed");
        assert!(token.is_some());

        let results = tvdb.search("Yuyushiki").await.expect("search failed");
        assert!(!results.is_empty());

        let series_id = results
            .iter()
            .find_map(|value| {
                value
                    .get("tvdb_id")
                    .or_else(|| value.get("id"))
                    .or_else(|| value.get("objectID"))
                    .or_else(|| value.get("objectId"))
                    .or_else(|| value.get("object_id"))
                    .and_then(|value| value.as_i64())
                    .or_else(|| {
                        value
                            .get("tvdb_id")
                            .or_else(|| value.get("id"))
                            .or_else(|| value.get("objectID"))
                            .or_else(|| value.get("objectId"))
                            .or_else(|| value.get("object_id"))
                            .and_then(|value| value.as_str())
                            .and_then(|value| value.parse::<i64>().ok())
                    })
            })
            .expect("missing tvdb id");

        let series = tvdb
            .get_series_extended(series_id)
            .await
            .expect("series extended failed");
        assert!(series.is_some());

        let artworks = tvdb
            .get_series_artworks(series_id, None, None)
            .await
            .expect("series artworks failed");
        assert!(artworks.is_some());

        let movie = tvdb
            .get_movie_extended(16609)
            .await
            .expect("movie extended failed");
        assert!(movie.is_some());
    }
}
