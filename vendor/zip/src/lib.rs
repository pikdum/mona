use std::io::{Read, Seek};

pub use zip9::result;

pub struct ZipArchive<R>(zip9::ZipArchive<R>);

impl<R: Read + Seek> ZipArchive<R> {
    pub fn new(reader: R) -> Result<Self, result::ZipError> {
        zip9::ZipArchive::new(reader).map(Self)
    }

    pub fn len(&self) -> usize {
        self.0.len()
    }

    pub fn by_index(&mut self, index: usize) -> Result<ZipFile<'_, R>, result::ZipError> {
        self.0.by_index(index).map(ZipFile)
    }
}

pub struct ZipFile<'a, R: Read>(zip9::read::ZipFile<'a, R>);

impl<R: Read> ZipFile<'_, R> {
    pub fn enclosed_name(&self) -> Option<std::path::PathBuf> {
        self.0.enclosed_name()
    }

    pub fn name(&self) -> std::borrow::Cow<'_, str> {
        self.0.name().unwrap_or_default()
    }

    pub fn unix_mode(&self) -> Option<u32> {
        self.0.unix_mode()
    }
}

impl<R: Read> Read for ZipFile<'_, R> {
    fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
        self.0.read(buffer)
    }
}
