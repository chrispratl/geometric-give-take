# Geometric Give and Take

Helper program for exploring the geometric Give and Take game as discussed in my Master's thesis.

## Basic setup

- Copy `.env.sample` to `.env`
- `docker compose up -d --build`
- Open `localhost:8080`, login to the Postgres database with User `postgres` and the password provided in `.env`
- For further exploration, run `docker compose run --rm -it cli python` and then `import cli`, and explore the methods defined in the `cli` module

## Helpful SQL commands

- Find assignments whose winner is unknown
  ```sql
  select * from positions_3
      where winner = 'bob  ' -- Note: Two spaces are on purpose here!
      and ((metadata ->> 'bob-win') != 'simple-win')
      and ((metadata ->> 'bob-win') != '1-2-buckets')
      and ((metadata ->> 'bob-win') != '1d-win')
      and ((metadata ->> 'bob-win') != '1d-extended-win')
      and ((metadata ->> 'bob-win') != 'monovariant')
      and ((metadata ->> 'bob-win') != 'monovariant-extended')
  limit 20
  ```
