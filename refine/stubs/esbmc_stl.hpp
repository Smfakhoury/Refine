// refine — Minimal STL stubs for ESBMC bounded model checking.
//
// ESBMC uses a Clang frontend and supports lambdas natively.
// These stubs provide minimal vector/pair/algorithm support.
// No constructor on vector — avoids CBMC/ESBMC pointer-analysis UNSAT bugs.

#include <cassert>

extern void __VERIFIER_assume(int);

namespace std {

template<typename T>
class vector {
public:
    T* _data;
    unsigned int _size;
    unsigned int size() const { return _size; }
    bool empty() const { return _size == 0; }
    T& operator[](unsigned int i) { return _data[i]; }
    const T& operator[](unsigned int i) const { return _data[i]; }
    T& operator[](int i) { return _data[(unsigned int)i]; }
    const T& operator[](int i) const { return _data[(unsigned int)i]; }
    T& front() { return _data[0]; }
    T& back() { return _data[_size > 0 ? _size - 1 : 0]; }
    const T& front() const { return _data[0]; }
    const T& back() const { return _data[_size > 0 ? _size - 1 : 0]; }
    void push_back(const T&) { }
    void reserve(unsigned int) { }
    T* begin() { return _data; }
    T* end() { return _data + _size; }
    const T* begin() const { return _data; }
    const T* end() const { return _data + _size; }
};

template<typename T1, typename T2>
struct pair {
    T1 first;
    T2 second;
};

template<typename T>
T abs(T x) { return x < 0 ? -x : x; }

template<typename It>
bool is_sorted(It begin, It end) {
    if (begin == end) return true;
    It prev = begin;
    for (It it = begin + 1; it != end; ++it) {
        if (*it < *prev) return false;
        prev = it;
    }
    return true;
}

template<typename It, typename Pred>
bool all_of(It begin, It end, Pred p) {
    for (It it = begin; it != end; ++it)
        if (!p(*it)) return false;
    return true;
}

template<typename It, typename Pred>
bool any_of(It begin, It end, Pred p) {
    for (It it = begin; it != end; ++it)
        if (p(*it)) return true;
    return false;
}

template<typename It, typename Pred>
bool none_of(It begin, It end, Pred p) {
    for (It it = begin; it != end; ++it)
        if (p(*it)) return false;
    return true;
}

template<typename It, typename T>
It find(It begin, It end, const T& val) {
    for (It it = begin; it != end; ++it)
        if (*it == val) return it;
    return end;
}

template<typename It, typename Pred>
It find_if(It begin, It end, Pred p) {
    for (It it = begin; it != end; ++it)
        if (p(*it)) return it;
    return end;
}

template<typename It, typename T>
int count(It begin, It end, const T& val) {
    int c = 0;
    for (It it = begin; it != end; ++it)
        if (*it == val) ++c;
    return c;
}

template<typename It, typename Pred>
int count_if(It begin, It end, Pred p) {
    int c = 0;
    for (It it = begin; it != end; ++it)
        if (p(*it)) ++c;
    return c;
}

template<typename It1, typename It2>
bool equal(It1 b1, It1 e1, It2 b2) {
    while (b1 != e1) {
        if (*b1 != *b2) return false;
        ++b1; ++b2;
    }
    return true;
}

template<typename It1, typename It2>
bool equal(It1 b1, It1 e1, It2 b2, It2 e2) {
    while (b1 != e1 && b2 != e2) {
        if (*b1 != *b2) return false;
        ++b1; ++b2;
    }
    return b1 == e1 && b2 == e2;
}

template<typename It>
void sort(It begin, It end) { /* stub */ }

template<typename It>
It min_element(It begin, It end) {
    It best = begin;
    for (It it = begin + 1; it != end; ++it)
        if (*it < *best) best = it;
    return best;
}

template<typename It>
It max_element(It begin, It end) {
    It best = begin;
    for (It it = begin + 1; it != end; ++it)
        if (*it > *best) best = it;
    return best;
}

template<typename It>
It adjacent_find(It begin, It end) {
    if (begin == end) return end;
    It prev = begin;
    for (It it = begin + 1; it != end; ++it) {
        if (*it == *prev) return prev;
        prev = it;
    }
    return end;
}

template<typename It, typename T>
T accumulate(It begin, It end, T init) {
    for (It it = begin; it != end; ++it)
        init = init + *it;
    return init;
}

template<typename T>
struct not_equal_to {
    bool operator()(const T& a, const T& b) const { return a != b; }
};

template<typename T>
void swap(T& a, T& b) { T t = a; a = b; b = t; }

template<typename T>
struct numeric_limits {
    static T min() { return T(); }
    static T max() { return T(); }
    static T epsilon() { return T(); }
};

typedef unsigned long size_t;
using nullptr_t = decltype(nullptr);

} // namespace std

// Math stubs
double sqrt(double x) { return x; }
double pow(double x, double y) { return x; }
double fabs(double x) { return x < 0 ? -x : x; }
double floor(double x) { return x; }
double ceil(double x) { return x; }
double round(double x) { return x; }
double log(double x) { return x; }
double log2(double x) { return x; }
int abs(int x) { return x < 0 ? -x : x; }

namespace std {
using ::abs;
using ::fabs;
using ::floor;
using ::ceil;
using ::round;
using ::log;
using ::log2;
using ::sqrt;
using ::pow;
}

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif
#define EPSILON 1e-9
// ---- End STL stubs ----
