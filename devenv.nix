{
  pkgs,
  lib,
  config,
  ...
}:
{
  dotenv.enable = true;

  # https://devenv.sh/languages/
  languages = {
    rust.enable = true;
  };

  # https://devenv.sh/git-hooks/
  git-hooks.hooks = {
    rustfmt.enable = true;
  };

  # See full reference at https://devenv.sh/reference/options/
}
