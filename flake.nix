{
  description = "Application packaged using poetry2nix";

  inputs = {
    flake-utils.url = "github:numtide/flake-utils";
    poetry2nix = {
      url = "github:nix-community/poetry2nix";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.flake-utils.follows = "flake-utils";
    };
  };

  outputs = { self, nixpkgs, flake-utils, poetry2nix }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        inherit (poetry2nix.lib.mkPoetry2Nix { inherit pkgs; })
          mkPoetryApplication mkPoetryScriptsPackage;
        myapp = mkPoetryApplication {
          projectDir = self;
          preferWheels = true;
        };
        myAppScripts = mkPoetryScriptsPackage {
          projectDir = self;
          python = pkgs.python311;
        };
      in
      {
        packages.default = pkgs.myapp;
        devShells.default = pkgs.mkShell {
          inputsFrom = [ myapp pkgs.poetry ];
          buildInputs = [ myAppScripts ];
        };
        legacyPackages = pkgs;
      }
    );
}

