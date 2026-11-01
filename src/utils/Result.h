#ifndef RESULT_H
#define RESULT_H

#include <QString>
#include <concepts>
#include <optional>
#include <stdexcept>
#include <type_traits>
#include <utility>
#include <variant>

/**
 * @brief Type-safe, monadic Result type for modern C++ error handling.
 *
 * Provides std::expected-like semantics for C++20 across AppCore and plugins,
 * avoiding output parameters (e.g. bool foo(..., QString *error)) and
 * unhandled exceptions across module boundaries.
 *
 * Supports both value types Result<T, E> and void operations Result<void, E>.
 */
namespace core {

struct Unit {};

template <typename E>
class ErrorWrapper {
public:
    explicit ErrorWrapper(E err) : m_error(std::move(err)) {}
    const E &error() const & { return m_error; }
    E &error() & { return m_error; }
    E &&error() && { return std::move(m_error); }
private:
    E m_error;
};

template <typename E = QString>
inline ErrorWrapper<std::decay_t<E>> Err(E &&error) {
    return ErrorWrapper<std::decay_t<E>>(std::forward<E>(error));
}

template <typename T, typename E = QString>
class [[nodiscard]] Result {
public:
    using value_type = T;
    using error_type = E;

    // Constructors for Ok value
    Result(const T &val) : m_data(std::in_place_index<0>, val) {}
    Result(T &&val) : m_data(std::in_place_index<0>, std::move(val)) {}

    // Constructor for Error
    Result(ErrorWrapper<E> err) : m_data(std::in_place_index<1>, std::move(err.error())) {}

    static Result ok(T val) { return Result(std::move(val)); }
    static Result err(E error) { return Result(Err(std::move(error))); }

    [[nodiscard]] bool isOk() const noexcept { return m_data.index() == 0; }
    [[nodiscard]] bool isError() const noexcept { return m_data.index() == 1; }
    [[nodiscard]] explicit operator bool() const noexcept { return isOk(); }

    [[nodiscard]] const T &value() const & {
        if (isError()) throw std::runtime_error("Attempted to access value on Result::Err");
        return std::get<0>(m_data);
    }

    [[nodiscard]] T &value() & {
        if (isError()) throw std::runtime_error("Attempted to access value on Result::Err");
        return std::get<0>(m_data);
    }

    [[nodiscard]] T &&value() && {
        if (isError()) throw std::runtime_error("Attempted to access value on Result::Err");
        return std::get<0>(std::move(m_data));
    }

    [[nodiscard]] const E &error() const & {
        if (isOk()) throw std::runtime_error("Attempted to access error on Result::Ok");
        return std::get<1>(m_data);
    }

    [[nodiscard]] E &error() & {
        if (isOk()) throw std::runtime_error("Attempted to access error on Result::Ok");
        return std::get<1>(m_data);
    }

    [[nodiscard]] E &&error() && {
        if (isOk()) throw std::runtime_error("Attempted to access error on Result::Ok");
        return std::get<1>(std::move(m_data));
    }

    template <typename U>
    [[nodiscard]] T valueOr(U &&defaultValue) const & {
        if (isOk()) return std::get<0>(m_data);
        return static_cast<T>(std::forward<U>(defaultValue));
    }

    template <typename U>
    [[nodiscard]] T valueOr(U &&defaultValue) && {
        if (isOk()) return std::get<0>(std::move(m_data));
        return static_cast<T>(std::forward<U>(defaultValue));
    }

    // Monadic map: transforms T -> U if Ok
    template <typename Fn>
    auto map(Fn &&fn) const & -> Result<std::invoke_result_t<Fn, const T &>, E> {
        using NewT = std::invoke_result_t<Fn, const T &>;
        if (isOk()) {
            return Result<NewT, E>::ok(fn(std::get<0>(m_data)));
        }
        return Result<NewT, E>::err(std::get<1>(m_data));
    }

    // Monadic andThen: transforms T -> Result<U, E> if Ok
    template <typename Fn>
    auto andThen(Fn &&fn) const & -> std::invoke_result_t<Fn, const T &> {
        if (isOk()) {
            return fn(std::get<0>(m_data));
        }
        using RetType = std::invoke_result_t<Fn, const T &>;
        return RetType::err(std::get<1>(m_data));
    }

    // Monadic mapError: transforms E -> NewE if Err
    template <typename Fn>
    auto mapError(Fn &&fn) const & -> Result<T, std::invoke_result_t<Fn, const E &>> {
        using NewE = std::invoke_result_t<Fn, const E &>;
        if (isError()) {
            return Result<T, NewE>::err(fn(std::get<1>(m_data)));
        }
        return Result<T, NewE>::ok(std::get<0>(m_data));
    }

private:
    std::variant<T, E> m_data;
};

/**
 * @brief Specialization of Result for void success value.
 */
template <typename E>
class [[nodiscard]] Result<void, E> {
public:
    using value_type = void;
    using error_type = E;

    Result() : m_error(std::nullopt) {}
    Result(ErrorWrapper<E> err) : m_error(std::move(err.error())) {}

    static Result ok() { return Result(); }
    static Result err(E error) { return Result(Err(std::move(error))); }

    [[nodiscard]] bool isOk() const noexcept { return !m_error.has_value(); }
    [[nodiscard]] bool isError() const noexcept { return m_error.has_value(); }
    [[nodiscard]] explicit operator bool() const noexcept { return isOk(); }

    void value() const {
        if (isError()) throw std::runtime_error("Attempted to access value on Result<void>::Err");
    }

    [[nodiscard]] const E &error() const & {
        if (isOk()) throw std::runtime_error("Attempted to access error on Result<void>::Ok");
        return *m_error;
    }

    [[nodiscard]] E &error() & {
        if (isOk()) throw std::runtime_error("Attempted to access error on Result<void>::Ok");
        return *m_error;
    }

    [[nodiscard]] E &&error() && {
        if (isOk()) throw std::runtime_error("Attempted to access error on Result<void>::Ok");
        return *std::move(m_error);
    }

    template <typename Fn>
    auto map(Fn &&fn) const -> Result<std::invoke_result_t<Fn>, E> {
        using NewT = std::invoke_result_t<Fn>;
        if (isOk()) {
            if constexpr (std::is_void_v<NewT>) {
                fn();
                return Result<void, E>::ok();
            } else {
                return Result<NewT, E>::ok(fn());
            }
        }
        return Result<NewT, E>::err(*m_error);
    }

    template <typename Fn>
    auto andThen(Fn &&fn) const -> std::invoke_result_t<Fn> {
        if (isOk()) {
            return fn();
        }
        using RetType = std::invoke_result_t<Fn>;
        return RetType::err(*m_error);
    }

private:
    std::optional<E> m_error;
};

} // namespace core

#endif // RESULT_H

